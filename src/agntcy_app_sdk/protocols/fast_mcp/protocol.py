# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import os
from typing import Any, Optional, Union

import httpx
import urllib.parse
import uvicorn
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.fast_mcp.client import MCPClient
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from mcp.server.fastmcp import FastMCP

# Configure logging for the application
configure_logging()
logger = get_logger(__name__)


class FastMCPProtocol(MCPProtocol):
  """
  Protocol implementation for FastMCP.

  This class provides methods to bind a FastMCP server, set up an ingress handler,
  create a client, and handle messages. It extends the MCPProtocol base class.
  """

  def __init__(self):
    """
    Initialize the FastMCPProtocol instance.
    """
    super().__init__()

  def type(self) -> str:
    """
    Return the protocol type.

    :return: Protocol type as a string.
    """
    return "FastMCP"

  def bind_server(self, server: FastMCP) -> None:
    """
    Bind a FastMCP server instance to the protocol.

    :param server: Instance of FastMCP server.
    :raises TypeError: If the provided server is not an instance of FastMCP.
    """

    if not isinstance(server, FastMCP):
      raise TypeError("Provided server is not an instance of FastMCP")
    self._server = server

  async def setup_ingress_handler(self) -> None:
    """
    Set up the ingress handler for the FastMCP server.

    This method initializes the ASGI application and starts the server using Uvicorn.
    :raises ValueError: If the FastMCP server is not bound to the protocol.
    """
    if not self._server:
      raise ValueError("FastMCP server is not bound to the protocol.")

    self._app = self._server.streamable_http_app()

    config = uvicorn.Config(
      self._app,
      host=os.getenv("FAST_MCP_HOST", "localhost"),
      port=int(os.getenv("FAST_MCP_PORT", 8000)),
      timeout_graceful_shutdown=3,
      lifespan="on",
    )
    await uvicorn.Server(config).serve()


  async def create_client(
          self,
          url: str,
          topic: Optional[str] = None,
          transport: Optional[BaseTransport] = None,
          route_path: Optional[str] = None,
          auth: Union[httpx.Auth, str, None] = None,
          **kwargs,
  ) -> MCPClient:
    """
    Create an MCP client for interacting with the FastMCP server.

    :param url: Base URL of the server.
    :param topic: Optional topic for the client.
    :param transport: Optional transport instance.
    :param route_path: Optional route path for the client.
    :param auth: Optional HTTP authentication (httpx.Auth or bearer token string).
    :param kwargs: Additional arguments for the MCPClient.
    :return: An instance of MCPClient.
    :raises ValueError: If the URL is not provided.
    :raises RuntimeError: If the client initialization fails.

    References:
    - Sequence diagram for transport initialization:
      https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#sequence-diagram
    """
    if not url:
      raise ValueError("URL must be provided")

    parsed_url = urllib.parse.urlparse(url)
    base_url = parsed_url._replace(path="").geturl()
    final_path = route_path or parsed_url.path or "/"
    full_url = urllib.parse.urljoin(base_url, final_path)

    # Prepare headers
    headers = {
      "Content-Type": "application/json",
      "Accept": "application/json, text/event-stream",
    }

    # - For Basic Auth, pass `auth=httpx.BasicAuth("username", "password")`
    # - For Bearer Token, pass `auth="your-bearer-token"` (a string)
    # This design allows flexible authentication methods without affecting existing clients.
    client_auth = None
    if isinstance(auth, str):
      headers["Authorization"] = f"Bearer {auth}"
    elif auth is not None:
      client_auth = auth  # pass httpx.Auth instance directly

    client = httpx.AsyncClient(base_url=full_url, timeout=10.0, auth=client_auth)

    try:
      init_payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "v1",
          "capabilities": {"experimental": {}},
          "clientInfo": {"name": "agntcy-client", "version": "1.0.0"},
        },
      }

      init_response = await client.post(
        full_url,
        headers=headers,
        json=init_payload,
      )
      logger.debug(f"Initialize response: {init_response.status_code}")

      session_id = init_response.headers.get("Mcp-Session-Id")
      if not session_id:
        raise RuntimeError("Missing Mcp-Session-Id in response")

      logger.debug(f"Session ID: {session_id}")

      # Add session id header for notifications
      notify_headers = headers.copy()
      notify_headers["Mcp-Session-Id"] = session_id

      await client.post(
        full_url,
        headers=notify_headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
      )

    except httpx.RequestError as e:
      logger.error(f"HTTP error during client initialization: {e}")
      raise RuntimeError(f"Failed to initialize MCP client: {e}") from e
    finally:
      await client.aclose()

    return MCPClient(
      session_id=session_id,
      transport=transport,
      topic=topic,
      route_path=final_path,
      **kwargs,
    )

  async def handle_message(self, message: Message, timeout: int = 15) -> Message:
    """
    Handle an incoming message and return a response.

    :param message: The incoming message to process.
    :param timeout: Timeout for handling the message.
    :return: A response message.
    :raises RuntimeError: If message handling fails.
    """
    assert self._app is not None, "ASGI app is not initialized"

    try:
      # Parse the message payload
      payload_dict = json.loads(message.payload.decode("utf-8"))

      # Construct the ASGI scope for the request
      scope = {
        "type": "http",
        "method": "POST",
        "path": message.route_path,
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

      # Create a receive function for the ASGI app
      def make_receive(payload: bytes):
        sent = False

        async def receive():
          nonlocal sent
          if not sent:
            sent = True
            return {"type": "http.request", "body": payload, "more_body": False}
          await asyncio.sleep(3600)

        return receive

      # Prepare response data and completion event
      response_data = {"status": None, "headers": None, "body": bytearray()}
      response_complete = asyncio.Event()

      # Define the send function for the ASGI app
      async def send(resp: dict[str, Any]):
        if resp["type"] == "http.response.start":
          response_data["status"] = resp["status"]
          response_data["headers"] = resp.get("headers", [])
        elif resp["type"] == "http.response.body":
          if "body" in resp:
            response_data["body"].extend(resp["body"])
          if not resp.get("more_body", False):
            response_complete.set()

      # Invoke the ASGI app and wait for the response
      await self._app(scope, make_receive(payload_bytes), send)
      await response_complete.wait()

      # Extract the payload from the response body
      body = bytes(response_data["body"]).decode("utf-8").strip()

      for line in body.splitlines():
        if line.startswith("data: "):
          json_data_str = line.removeprefix("data: ").strip()
          payload = json.dumps(json.loads(json_data_str)).encode("utf-8")
          break
      else:
        raise ValueError("Missing 'data:' line in SSE response")

      return Message(type="MCPResponse", payload=payload, reply_to=message.reply_to)

    except Exception as e:
      logger.error(f"Error in handle_message: {e}")
      raise RuntimeError(f"Message handling failed: {e}")
