# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.base import ServerHandler
from agntcy_app_sdk.semantic.mcp.protocol import MCPProtocol
from agntcy_app_sdk.transport.base import BaseTransport

from mcp.server.lowlevel import Server as MCPServer
from typing import Optional

logger = get_logger(__name__)


class MCPServerHandler(ServerHandler):
    """Server-side handler for the MCP protocol. Requires transport + topic."""

    def __init__(
        self,
        server: MCPServer,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
    ):
        if topic is None or topic == "":
            raise ValueError("Topic must be provided for MCP server")

        super().__init__(server, transport=transport, topic=topic)
        self._protocol = MCPProtocol()

    def protocol_type(self) -> str:
        return "MCP"

    async def setup(self) -> None:
        """Full lifecycle: transport.setup() → set_callback() → subscribe() → protocol.setup()."""
        if self._transport is None:
            raise ValueError("Transport must be set before running MCP handler.")

        # Transport setup
        await self._transport.setup()

        # Bind server to protocol
        self._protocol.bind_server(self._managed_object)

        # Set callback for incoming messages
        self._transport.set_callback(self._protocol.handle_message)

        # Subscribe to topic
        await self._transport.subscribe(self._topic)

        # Protocol-level setup (MCP server run, streams, futures)
        await self._protocol.setup()

        logger.info(f"MCP handler started on topic: {self._topic}")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.info("MCP transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing MCP transport: {e}")
