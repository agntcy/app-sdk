# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.base import ServerHandler
from agntcy_app_sdk.semantic.fast_mcp.protocol import FastMCPProtocol
from agntcy_app_sdk.transport.base import BaseTransport

from mcp.server.fastmcp import FastMCP
from typing import Optional

logger = get_logger(__name__)


class FastMCPServerHandler(ServerHandler):
    """Server-side handler for FastMCP. Transport is optional — Uvicorn always runs."""

    def __init__(
        self,
        server: FastMCP,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
    ):
        # Validate: if transport is given, topic is required
        if transport is not None and (topic is None or topic == ""):
            raise ValueError("Topic must be provided when transport is set for FastMCP")

        super().__init__(server, transport=transport, topic=topic)
        self._protocol = FastMCPProtocol()

    def protocol_type(self) -> str:
        return "FastMCP"

    async def setup(self) -> None:
        """Optionally wire transport, then run protocol.setup() (Uvicorn)."""
        # Bind server to protocol
        self._protocol.bind_server(self._managed_object)

        if self._transport is not None:
            # Transport setup
            await self._transport.setup()

            # Set callback for incoming messages
            self._transport.set_callback(self._protocol.handle_message)

            # Subscribe to topic
            await self._transport.subscribe(self._topic)

            logger.info(f"FastMCP handler wired transport on topic: {self._topic}")

        # Protocol-level setup (Uvicorn server)
        await self._protocol.setup()

        logger.info("FastMCP handler started.")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.info("FastMCP transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing FastMCP transport: {e}")
