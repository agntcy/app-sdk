# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict

from agntcy_app_sdk.protocols.message import Message


class MCPClient:
  """
  MCPClient is responsible for interacting with the MCP protocol over a given transport.
  It provides methods to call tools and list available tools using JSON-RPC requests.

  Attributes:
      transport: The transport instance used for communication.
      session_id: The session identifier for the MCP protocol.
      topic: The topic to publish messages to.
  """

  def __init__(self, transport: None, session_id: str, topic: str):
    """
    Initialize the MCPClient.

    Args:
        transport: The transport instance used for communication.
        session_id: The session identifier for the MCP protocol.
        topic: The topic to publish messages to.
    """
    self.session_id = session_id
    self.transport = transport
    self.topic = topic

  async def __aenter__(self):
    """
    Enter the asynchronous context for the MCPClient.

    Returns:
        MCPClient instance.
    """
    return self

  async def __aexit__(self, exc_type, exc, tb):
    """
    Exit the asynchronous context for the MCPClient.

    Ensures the transport is closed properly.
    """
    if self.transport:
      try:
        await self.transport.close()
      except Exception as e:
        # Log the error while closing the transport
        print(f"[error] Error while closing transport: {e}")

  async def call_tool(
          self, name: str, arguments: Dict[str, Any], request_id: int = 1
  ) -> Dict[str, Any]:
    """
    Call a tool using the MCP protocol.

    Args:
        name: The name of the tool to call.
        arguments: The arguments to pass to the tool.
        request_id: The unique identifier for the JSON-RPC request.

    Returns:
        The result of the tool call.

    Raises:
        RuntimeError: If the response contains an error.
    """
    try:
      # Prepare the JSON-RPC request payload
      rpc_request_payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {
          "name": name,
          "arguments": arguments,
        },
      }

      # Set required headers
      headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Mcp-Session-Id": self.session_id,
      }

      # Translate to internal Message format
      message = self.message_translator(request=rpc_request_payload, headers=headers)

      # Publish the message and await the response
      response = await self.transport.publish(topic=self.topic, message=message, respond=True)

      # Decode and parse response payload
      payload = json.loads(response.payload.decode("utf-8"))

      if "error" in payload:
        raise RuntimeError(f"[error] tools/call error: {payload['error']}")

      return payload["result"]
    except Exception as e:
      print(f"[error] Failed to call tool '{name}': {e}")
      raise

  async def list_tools(self, request_id: int = 1) -> dict:
    """
    List available tools using the MCP protocol.

    Args:
        request_id: The unique identifier for the JSON-RPC request.

    Returns:
        A dictionary containing the list of tools.

    Raises:
        RuntimeError: If the response contains an error.
    """
    try:
      # Prepare the JSON-RPC request payload
      rpc_request_payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/list",
      }

      # Set required headers
      headers = {"Mcp-Session-Id": self.session_id}

      # Translate to internal Message format
      message = self.message_translator(request=rpc_request_payload, headers=headers)

      # Publish the message and await the response
      response = await self.transport.publish(topic=self.topic, message=message, respond=True)

      # Decode and parse response payload
      payload = json.loads(response.payload.decode("utf-8"))

      if "error" in payload:
        raise RuntimeError(f"[error] tools/list error: {payload['error']}")

      return payload["result"]
    except Exception as e:
      print(f"[error] Failed to list tools: {e}")
      raise

  def message_translator(self, request: dict[str, Any], headers: dict[str, Any] | None = None) -> Message:
    """
    Translate a JSON-RPC request into an internal Message format.

    Args:
        request: The JSON-RPC request payload.
        headers: Optional headers to include in the message.

    Returns:
        A Message instance.

    Raises:
        ValueError: If headers are not a dictionary.
    """
    if headers is None:
      headers = {}
    if not isinstance(headers, dict):
      raise ValueError("[error] Headers must be a dictionary")

    return Message(
      type="MCPRequest",
      payload=json.dumps(request),
      route_path="/",  # JSON-RPC path
      method="POST",  # A2A JSON-RPC will always use POST
      headers=headers,
    )