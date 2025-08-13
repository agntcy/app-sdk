# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
from starlette.types import Scope
from typing import Dict, Any
from typing import Any,  Callable
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession
import mcp.types as types
import json

from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage
from fastmcp import Client, FastMCP as FastMCPServer

import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from contextlib import asynccontextmanager
import asyncio

configure_logging()
logger = get_logger(__name__)

class FastMCPProtocol(MCPProtocol):
  """
  FastMCPProtocol bridges the MCPProtocol with the FastMCP server.
  It provides methods to bind the FastMCP server and handle messages.
  """

  def __init__(self, name: str = "FastMCPProtocol", instructions: str | None = None, **kwargs: Any):
    super().__init__()

  def type(self):
    """Return the protocol type identifier."""
    return "FastMCP"

  def bind_server(self, server: FastMCP) -> None:
    """
    Bind an MCP server instance to this protocol for handling incoming requests.
    """
    # Validate server type


    # Handle FastMCP wrapper by extracting the underlying server
    self._server = server


  async def setup_ingress_handler(self) -> None:
    """
    Set up the ingress handler to process incoming FastMCP requests.
    """
    if not self._server or (not isinstance(self._server, FastMCPServer) and not isinstance(self._server, FastMCP)):
      raise ValueError("FastMCP server is not bound to the protocol")

    f = FastMCPServer()
    @f.tool()
    async def get_forecast(location: str) -> str:
      return "Temperature: 30°C\n" "Humidity: 50%\n" "Condition: Sunny\n"
    # self._app = f.http_app(transport="streamable-http")
    await f.run_async(transport="streamable-http")


  async def handle_message(self, message: Message, timeout: int = 15) -> Message:
    rpc_message = types.JSONRPCMessage.model_validate_json(message.payload.decode())
    rpc_message_dict = rpc_message.dict()  # Convert to dictionary
    logger.info(f"Handling message: {rpc_message_dict} {message.reply_to}")

    client = Client("http://localhost:8000/mcp")

    async with client:
      await client.ping()

      if rpc_message_dict.get("method") == "tools/list":
        tools = await client.list_tools()
        logger.info(f"Available tools: {tools}")
        # Convert tools to a JSON-serializable format
        tools_serializable = [tool.dict() for tool in tools]
        response_payload = {
          "jsonrpc": "2.0",
          "result": {"tools": tools_serializable},  # Wrap the list in an object
          "id": rpc_message_dict.get("id"),
        }

      elif rpc_message_dict.get("method") == "tools/call":
        tool_name = rpc_message_dict.get("params", {}).get("name")
        arguments = rpc_message_dict.get("params", {}).get("arguments", {})
        result = await client.call_tool(tool_name, arguments)
        logger.info(f"Tool call result: {result}")

        # Ensure result is JSON serializable
        if isinstance(result, list):
          result = {"result": result}  # Wrap the list in an object
        elif hasattr(result, "to_dict"):  # Check if result has a `to_dict` method
          result = result.to_dict()
        else:
          result = {"result": str(result)}  # Fallback to string representation

        response_payload = {
          "jsonrpc": "2.0",
          "result": result,
          "id": rpc_message_dict.get("id"),
        }

      else:
        response_payload = {
          "jsonrpc": "2.0",
          "error": {"code": -32601, "message": "Method not found"},
          "id": rpc_message_dict.get("id"),
        }

    msg = Message(
      type=str(types.JSONRPCMessage),
      payload=json.dumps(response_payload).encode(),
      reply_to=message.reply_to,
    )

    logger.info(f"Returning message: {msg}")

    return msg

  # async def handle_message(self, message: Message, timeout: int = 15) -> Message:
  #   """
  #   Handle an incoming MCP message and return the server's response.
  #
  #   This method processes incoming messages by:
  #   1. Deserializing JSON-RPC messages to MCP format
  #   2. Routing messages to the MCP server via an ASGI app
  #   3. Returning the server's response in SITL JSON-RPC format.
  #
  #   Args:
  #       message: The incoming message to process.
  #
  #   Returns:
  #       Message: The server's response message in SITL JSON-RPC format.
  #   """
  #   assert self._app is not None, "ASGI app is not set up"
  #
  #   # Deserialize the incoming JSON-RPC message
  #   rpc_message = types.JSONRPCMessage.model_validate_json(message.payload.decode())
  #   logger.info(f"Handling message: {rpc_message}")
  #
  #   # Prepare ASGI scope
  #   headers = [
  #     (key.encode("utf-8"), value.encode("utf-8") if isinstance(value, str) else value)
  #     for key, value in message.headers.items()
  #   ]
  #
  #   logger.info(f"Headers: {headers}")
  #   logger.info(f"Method: {message.method}")
  #
  #   scope: Scope = {
  #     "type": "http",
  #     "asgi": {"version": "3.0", "spec_version": "2.1"},
  #     "http_version": "1.1",
  #     "path": "/mcp",
  #     "method": "POST",
  #     "scheme": "http",
  #     "raw_path": b"/",  # Default path
  #     "query_string": b"",
  #     "headers": headers,
  #     "client": ("agntcy-bridge", 0),
  #     "server": ("agntcy-bridge", 0),
  #   }
  #
  #
  #   # Create the receive channel
  #   async def receive() -> Dict[str, Any]:
  #     return {
  #       "type": "http.request",
  #       "body": message.payload,
  #       "more_body": False,
  #     }
  #
  #   # Create the send channel
  #   response_data = {
  #     "status": None,
  #     "headers": None,
  #     "body": bytearray(),
  #   }
  #
  #   async def send(message: Dict[str, Any]) -> None:
  #     if message["type"] == "http.response.start":
  #       response_data["status"] = message["status"]
  #       response_data["headers"] = message.get("headers", [])
  #     elif message["type"] == "http.response.body":
  #       if "body" in message:
  #         response_data["body"].extend(message["body"])
  #
  #   # Call the ASGI application
  #   logger.info(f"Calling ASGI app with scope: {self._app}")
  #   await self._app(scope, receive, send)
  #
  #   # Parse the response body
  #   body = bytes(response_data["body"])
  #
  #   logger.info(f"Response body: {body}")
  #   try:
  #     body_obj = json.loads(body.decode("utf-8"))
  #     payload = json.dumps(body_obj).encode("utf-8")  # re-encode as bytes
  #   except (json.JSONDecodeError, UnicodeDecodeError):
  #     payload = body  # raw bytes
  #
  #   # Return the response in SITL JSON-RPC format
  #   return Message(
  #     type=str(types.JSONRPCMessage),
  #     payload=payload,
  #     reply_to=message.reply_to,
  #   )