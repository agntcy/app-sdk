# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict
from agntcy_app_sdk.protocols.message import Message


class MCPClient:
  def __init__(
          self,
          transport,
          session_id: str,
          topic: str,
          route_path: str = "/",
  ):
    """
    Initialize the MCPClient instance.

    :param transport: Transport instance for communication.
    :param session_id: Unique session identifier for MCP communication.
    :param topic: Topic to publish messages to.
    :param route_path: Route path for the MCP requests (default: "/").
    """
    self.transport = transport
    self.session_id = session_id
    self.topic = topic
    self.route_path = route_path

  async def __aenter__(self):
    """
    Enter the async context manager.

    :return: The MCPClient instance.
    """
    return self

  async def __aexit__(self, exc_type, exc, tb):
    """
    Exit the async context manager.

    Ensures the transport is closed properly.

    :param exc_type: Exception type, if any.
    :param exc: Exception instance, if any.
    :param tb: Traceback object, if any.
    """
    if self.transport:
      try:
        await self.transport.close()
      except Exception as e:
        print(f"[error] Transport close failed: {e}")

  def message_translator(self, request: dict[str, Any], headers: dict[str, Any] | None = None) -> Message:
    """
    Translate a request dictionary into a Message object.

    :param request: The request payload as a dictionary.
    :param headers: Optional headers for the request.
    :return: A `Message` object ready for transport.
    :raises ValueError: If headers are not a dictionary.
    """
    if headers is None:
      headers = {}
    if not isinstance(headers, dict):
      raise ValueError("Headers must be a dictionary")

    # Create a Message object with the provided request and headers
    return Message(
      type="MCPRequest",
      payload=json.dumps(request),  # Serialize the request payload to JSON
      route_path=self.route_path,  # Specify the route path for the request
      method="POST",  # HTTP method for the request
      headers=headers,  # Include headers in the message
    )

  async def call_tool(self, name: str, arguments: Dict[str, Any], request_id: int = 1) -> Dict[str, Any]:
    """
    Call a tool via MCP.

    :param name: Name of the tool to call.
    :param arguments: Arguments to pass to the tool.
    :param request_id: Optional request ID (default: 1).
    :return: The result of the tool call as a dictionary.
    :raises RuntimeError: If the tool call returns an error.
    :raises Exception: For unexpected errors during execution.
    """
    try:
      # Prepare the payload for the tool call
      payload = {
        "jsonrpc": "2.0",  # JSON-RPC version
        "id": request_id,  # Unique request ID
        "method": "tools/call",  # Method to call the tool
        "params": {
          "name": name,  # Tool name
          "arguments": arguments,  # Arguments for the tool
        },
      }

      # Define headers for the request
      headers = {
        "Content-Type": "application/json",  # Specify JSON content type
        "Accept": "application/json, text/event-stream",  # Acceptable response formats
        "Mcp-Session-Id": self.session_id,  # Include session ID for MCP
      }

      # Translate the payload and headers into a Message object
      message = self.message_translator(payload, headers)

      # Publish the message and await the response
      response = await self.transport.publish(self.topic, message, respond=True)

      # Decode the response payload
      data = json.loads(response.payload.decode("utf-8"))

      # Check for errors in the response
      if "error" in data:
        raise RuntimeError(f"[error] tools/call error: {data['error']}")

      # Return the result from the response
      return data["result"]

    except Exception as e:
      print(f"[error] Failed to call tool '{name}': {e}")
      raise

  async def list_tools(self, request_id: int = 1) -> dict:
    """
    List available tools via MCP.

    :param request_id: Optional request ID (default: 1).
    :return: A dictionary containing the list of tools.
    :raises RuntimeError: If the tools/list request returns an error.
    :raises Exception: For unexpected errors during execution.
    """
    try:
      # Prepare the payload for listing tools
      payload = {
        "jsonrpc": "2.0",  # JSON-RPC version
        "id": request_id,  # Unique request ID
        "method": "tools/list",  # Method to list tools
      }

      # Define headers for the request
      headers = {"Mcp-Session-Id": self.session_id}  # Include session ID for MCP

      # Translate the payload and headers into a Message object
      message = self.message_translator(payload, headers)

      # Publish the message and await the response
      response = await self.transport.publish(self.topic, message, respond=True)

      # Decode the response payload
      data = json.loads(response.payload.decode("utf-8"))

      # Check for errors in the response
      if "error" in data:
        raise RuntimeError(f"[error] tools/list error: {data['error']}")

      # Return the result from the response
      return data["result"]

    except Exception as e:
      print(f"[error] Failed to list tools: {e}")
      raise
