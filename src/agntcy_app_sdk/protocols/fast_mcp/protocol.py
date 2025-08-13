# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import os
from typing import Any, Optional

import httpx
import uvicorn
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.fast_mcp.client import MCPClient
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from fastmcp import FastMCP

# Configure logging for the application
configure_logging()
logger = get_logger(__name__)


class FastMCPProtocol(MCPProtocol):
  """
  FastMCPProtocol bridges the MCPProtocol with the FastMCP server.
  It provides methods to bind the FastMCP server, handle messages, and create clients.
  """

  def __init__(self):
    super().__init__()

  def type(self) -> str:
    """Return the protocol type identifier."""
    return "FastMCP"

  def bind_server(self, server: FastMCP) -> None:
    """
    Bind an MCP server instance to this protocol for handling incoming requests.

    Args:
        server: The FastMCP server instance to bind.

    Raises:
        TypeError: If the provided server is not an instance of FastMCP.
    """
    if not isinstance(server, FastMCP):
      raise TypeError("Provided server is not an instance of FastMCP")
    self._server = server

  async def setup_ingress_handler(self) -> None:
    """
    Set up the ingress handler to process incoming FastMCP requests using FastAPI.

    Raises:
        ValueError: If the FastMCP server is not bound to the protocol.
    """
    if not self._server:
      raise ValueError("FastMCP server is not bound to the protocol.")

    # Create the FastAPI application with the lifespan from the FastMCP server
    self._app = self._server.http_app(transport="streamable-http")

    config_kwargs: dict[str, Any] = {
      "timeout_graceful_shutdown": 3,
      "lifespan": "on",
    }

    host = os.getenv("FAST_MCP_HOST", "localhost")
    port = int(os.getenv("FAST_MCP_PORT", 8000))
    config = uvicorn.Config(self._app, host=host, port=port, **config_kwargs)
    server = uvicorn.Server(config)
    await server.serve()

  async def create_client(
          self,
          url: str,
          topic: str = None,
          transport: Optional[BaseTransport] = None,
          **kwargs,
  ) -> MCPClient:
    """
    Create and initialize an MCP client.

    Args:
        url: The base URL for the MCP client.
        topic: Optional topic for the client.
        transport: Optional transport instance.
        **kwargs: Additional arguments for the MCPClient.

    Returns:
        MCPClient: A ready-to-use MCP client.

    Raises:
        ValueError: If the URL is not provided.
        RuntimeError: If the server does not return a valid Mcp-Session-Id.
    """
    if url is None:
      raise ValueError("URL must be provided for MCP client initialization")

    client = httpx.AsyncClient(base_url=url, timeout=10.0)

    # Send `initialize` request
    init_payload = {
      "jsonrpc": "2.0",
      "id": 1,
      "method": "initialize",
      "params": {
        "protocolVersion": "v1",
        "capabilities": {
          "experimental": {}
        },
        "clientInfo": {
          "name": "agntcy-client",
          "version": "1.0.0"
        }
      }
    }

    try:
      init_response = await client.post(
        url,
        headers={
          "Content-Type": "application/json",
          "Accept": "application/json, text/event-stream",
        },
        json=init_payload,
      )
      logger.debug(f"Initialize response status: {init_response.status_code}")

      # Extract Mcp-Session-Id
      session_id = init_response.headers.get("Mcp-Session-Id")
      if not session_id:
        raise RuntimeError("Server did not return a valid Mcp-Session-Id")

      logger.debug(f"Session ID received: {session_id}")

      # Send `notifications/initialized` request
      await client.post(
        url,
        headers={
          "Content-Type": "application/json",
          "Accept": "application/json, text/event-stream",
          "Mcp-Session-Id": session_id,
        },
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
      )

    except httpx.RequestError as e:
      logger.error(f"HTTP request failed: {e}")
      raise RuntimeError(f"Failed to create MCP client: {e}") from e

    # Create and return the client
    return MCPClient(session_id=session_id, transport=transport, topic=topic, **kwargs)

  async def handle_message(self, message: Message, timeout: int = 15) -> Message:
    """
    Handle an incoming MCP message and return the server's response.

    Args:
        message: The incoming message to process.
        timeout: Timeout for handling the message (default: 15 seconds).

    Returns:
        Message: The server's response message.

    Raises:
        RuntimeError: If an error occurs while handling the message.
    """
    assert self._app is not None, "ASGI app is not set up"

    try:
      # Deserialize the incoming JSON-RPC message
      payload_str = message.payload.decode("utf-8")
      payload_dict = json.loads(payload_str)

      scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [
          (b"accept", b"application/json, text/event-stream"),
          (b"content-type", b"application/json"),
          (
            b"mcp-session-id",
            message.headers.get("Mcp-Session-Id", "default_session_id").encode("utf-8"),
          ),
        ],
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
      }

      payload_bytes = json.dumps(payload_dict).encode("utf-8")

      def make_receive(payload: bytes):
        sent = False

        async def receive():
          nonlocal sent
          if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
          else:
            await asyncio.sleep(3600)

        return receive

      receive = make_receive(payload_bytes)

      response_data = {"status": None, "headers": None, "body": bytearray()}
      response_complete = asyncio.Event()

      async def send(message: dict[str, Any]) -> None:
        message_type = message["type"]

        if message_type == "http.response.start":
          response_data["status"] = message["status"]
          response_data["headers"] = message.get("headers", [])

        elif message_type == "http.response.body":
          if "body" in message:
            response_data["body"].extend(message["body"])
          if not message.get("more_body", False):
            response_complete.set()

      await self._app(scope, receive, send)
      await response_complete.wait()

      body = bytes(response_data["body"])

      try:
        body_str = body.decode("utf-8").strip()
        for line in body_str.splitlines():
          if line.startswith("data: "):
            json_data_str = line.removeprefix("data: ").strip()
            body_obj = json.loads(json_data_str)
            payload = json.dumps(body_obj).encode("utf-8")
            break
        else:
          raise ValueError("No 'data:' line found in SSE response")

      except (json.JSONDecodeError, UnicodeDecodeError) as e:
        logger.error(f"Failed to decode JSON payload: {e}")
        payload = body  # Fall back to raw bytes

      return Message(type="MCPResponse", payload=payload, reply_to=message.reply_to)

    except Exception as e:
      logger.error(f"Error handling message: {e}")
      raise RuntimeError(f"Failed to handle message: {e}") from e
