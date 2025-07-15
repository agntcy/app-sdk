# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any

from mcp.client.streamable_http import streamablehttp_client

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol
from agntcy_app_sdk.transports.streamable_http.transport import StreamableHTTPTransport

configure_logging()
logger = get_logger(__name__)


class MCPProtocol(BaseAgentProtocol):
    """
    MCPProtocol is a placeholder for the MCP protocol implementation.
    It should define methods to create clients, receivers, and handle messages.
    """

    def type(self):
        return "MCP"

    async def create_client(
        self,
        url: str = None,
        transport: BaseTransport = None,
        **kwargs,
    ) -> StreamableHTTPTransport:
        """
        Create a client for the MCP protocol.
        """
        logger.info(f"Creating MCP client with URL: {url}")
        if not url:
            raise ValueError("MCP Server URL must be provided to create an MCP client")

        # overrides the transport to use StreamableHTTPTransport
        transport = StreamableHTTPTransport(endpoint=url)

        # Create a streamable HTTP client for MCP
        try:
            client = streamablehttp_client(url=url)
            await transport.connect(client)
            return transport
        except Exception as e:
            await transport.close()
            logger.error(f"Failed to create MCP client: {e}")
            raise

    def message_translator(self, request: Any) -> Message:
        """
        Translate a request into a Message object.
        This method should be implemented to convert the request format
        into the Message format used by the MCP protocol.
        """
        raise NotImplementedError(
            "Message translation is not implemented for MCP protocol"
        )

    def create_ingress_handler(self, *args, **kwargs) -> Any:
        """
        Create an ingress handler for the MCP protocol.
        This method should define how to handle incoming messages.
        """
        raise NotImplementedError(
            "Ingress handler creation is not implemented for MCP protocol"
        )
