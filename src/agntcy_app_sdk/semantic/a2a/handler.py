# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from agntcy_app_sdk.semantic.base import ServerHandler
from agntcy_app_sdk.transport.base import BaseTransport

from a2a.server.apps import A2AStarletteApplication
from typing import Optional

logger = get_logger(__name__)


class A2AServerHandler(ServerHandler):
    """Server-side handler for the A2A protocol. Requires a transport."""

    def __init__(
        self,
        server: A2AStarletteApplication,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
        directory: Optional[BaseAgentDirectory] = None,
    ):
        # Auto-derive topic from agent_card if not provided
        if topic is None or topic == "":
            topic = A2AProtocol.create_agent_topic(server.agent_card)

        super().__init__(server, transport=transport, topic=topic, directory=directory)
        self._protocol = A2AProtocol()

    def protocol_type(self) -> str:
        return "A2A"

    async def setup(self) -> None:
        """Full lifecycle: transport.setup() → set_callback() → subscribe() → directory → protocol.setup()."""
        if self._transport is None:
            raise ValueError("Transport must be set before running A2A handler.")

        # Transport setup
        await self._transport.setup()

        # Directory setup
        if self._directory:
            await self._directory.setup()

        # Bind server and create the protocol bridge
        self._protocol.bind_server(self._server)

        # Set callback for incoming messages
        self._transport.set_callback(self._protocol.handle_message)

        # Subscribe to topic
        await self._transport.subscribe(self._topic)

        # Push to directory if available
        if self._directory:
            await self._directory.push_agent_record(self._protocol.agent_record())

        # Protocol-level setup (ASGI bridge, tracing, etc.)
        await self._protocol.setup()

        logger.info(f"A2A handler started on topic: {self._topic}")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.info("A2A transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing A2A transport: {e}")
