# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.protocols.fast_mcp.config import FASTMCP_HOST, FASTMCP_PORT
import mcp.types as types
import json

from fastmcp import Client, FastMCP

# Configure logging for the application
configure_logging()
logger = get_logger(__name__)

class FastMCPProtocol(MCPProtocol):
  """
  FastMCPProtocol bridges the MCPProtocol with the FastMCP server.
  It provides methods to bind the FastMCP server and handle messages.
  """

  def __init__(self):
    super().__init__()
    self._server = None

  def type(self) -> str:
    """Return the protocol type identifier."""
    return "FastMCP"

  def bind_server(self, server: FastMCP) -> None:
    """
    Bind an MCP server instance to this protocol for handling incoming requests.

    Args:
        server: The FastMCP server instance to bind.
    """
    if not isinstance(server, FastMCP):
      raise TypeError("Provided server is not an instance of FastMCP")
    self._server = server
    logger.info("FastMCP server successfully bound to the protocol.")

  async def setup_ingress_handler(self) -> None:
    """
    Set up the ingress handler to process incoming FastMCP requests.
    """
    if not self._server:
      raise ValueError("FastMCP server is not bound to the protocol.")
    try:
      await self._server.run_streamable_http_async(host=FASTMCP_HOST, port=FASTMCP_PORT)
      logger.info(f"Ingress handler set up successfully on {FASTMCP_HOST}:{FASTMCP_PORT}.")
    except Exception as e:
      logger.error(f"Failed to set up ingress handler: {e}")
      raise

  async def handle_message(self, message: Message, timeout: int = 15) -> Message:
    """
    Handle an incoming MCP message and return the server's response.

    Args:
        message: The incoming message to process.
        timeout: Timeout for handling the message (default: 15 seconds).

    Returns:
        Message: The server's response message.
    """
    try:
      # Deserialize the incoming JSON-RPC message
      rpc_message = types.JSONRPCMessage.model_validate_json(message.payload.decode())
      rpc_message_dict = rpc_message.model_dump()  # Convert to dictionary
      logger.debug(f"Received RPC message: {rpc_message_dict}")

      # Initialize FastMCP client
      client = Client(f'http://{FASTMCP_HOST}:{FASTMCP_PORT}/mcp')

      async with client:
        if rpc_message_dict.get("method") == "tools/list":
          # Handle tools listing
          tools = await client.list_tools()
          logger.info(f"Available tools: {tools}")
          tools_serializable = [tool.dict() for tool in tools]
          response_payload = {
            "jsonrpc": "2.0",
            "result": {"tools": tools_serializable},
            "id": rpc_message_dict.get("id"),
          }

        elif rpc_message_dict.get("method") == "tools/call":
          # Handle tool invocation
          tool_name = rpc_message_dict.get("params", {}).get("name")
          arguments = rpc_message_dict.get("params", {}).get("arguments", {})
          result = await client.call_tool(tool_name, arguments)
          logger.info(f"Tool '{tool_name}' executed with arguments: {arguments}. Result: {result}")

          # Ensure result is JSON serializable
          if isinstance(result, list):
            result = {"result": result}
          elif hasattr(result, "to_dict"):
            result = result.to_dict()
          else:
            result = {"result": str(result)}

          response_payload = {
            "jsonrpc": "2.0",
            "result": result,
            "id": rpc_message_dict.get("id"),
          }

        else:
          # Handle unknown method
          logger.warning(f"Unknown method '{rpc_message_dict.get('method')}' received.")
          response_payload = {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "Method not found"},
            "id": rpc_message_dict.get("id"),
          }

      # Create response message
      msg = Message(
        type=str(types.JSONRPCMessage),
        payload=json.dumps(response_payload).encode(),
        reply_to=message.reply_to,
      )
      logger.debug(f"Returning response message: {msg}")
      return msg

    except json.JSONDecodeError as e:
      logger.error(f"Failed to decode JSON payload: {e}")
      raise ValueError("Invalid JSON payload") from e
    except Exception as e:
      logger.error(f"Error while handling message: {e}")
      raise
