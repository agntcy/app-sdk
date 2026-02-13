# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard
from typing import Optional

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.server.patterns_server import A2APatternsServer
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)

# Maps BaseTransport.type() → (preferred_transport name, URI scheme)
_TRANSPORT_NAME_MAP: dict[str, tuple[str, str]] = {
    "SLIM": ("slimpatterns", "slim"),
    "NATS": ("natspatterns", "nats"),
}


class A2APatternsServerHandler(BaseA2AServerHandler):
    """A2A handler that bridges an ``A2AStarletteApplication`` over a
    ``BaseTransport`` (SLIM or NATS pub-sub patterns).

    Sets ``preferred_transport`` to ``"slimpatterns"`` or
    ``"natspatterns"`` depending on the transport type.
    """

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
            topic = A2APatternsServer.create_agent_topic(server.agent_card)

        super().__init__(server, transport=transport, topic=topic, directory=directory)
        self._protocol = A2APatternsServer()

    # -- agent_card property (required by BaseA2AServerHandler) -----------

    @property
    def agent_card(self) -> AgentCard:
        return self._managed_object.agent_card

    # -- Lifecycle --------------------------------------------------------

    async def setup(self) -> None:
        """Full lifecycle: transport.setup() → set_callback() → subscribe() → directory → protocol.setup()."""
        if self._transport is None:
            raise ValueError("Transport must be set before running A2A handler.")

        # Stamp preferred_transport and card.url before anything else
        transport_type = self._transport.type()
        transport_entry = _TRANSPORT_NAME_MAP.get(transport_type)
        if transport_entry:
            transport_name, scheme = transport_entry
            self._set_preferred_transport(transport_name)
            # Encode the topic into card.url so clients can derive it
            old_url = self._managed_object.agent_card.url
            new_url = f"{scheme}://{self._topic}"
            logger.info(
                "Overwriting card.url '%s' → '%s' for %s transport",
                old_url,
                new_url,
                transport_name,
            )
            self._managed_object.agent_card.url = new_url
        else:
            logger.warning(
                f"Unknown transport type '{transport_type}'; "
                "preferred_transport not set."
            )

        # Transport setup
        await self._transport.setup()

        # Directory setup
        if self._directory:
            await self._directory.setup()

        # Bind server and create the protocol bridge
        self._protocol.bind_server(self._managed_object)

        # Set callback for incoming messages
        self._transport.set_callback(self._protocol.handle_message)

        # Subscribe to topic
        await self._transport.subscribe(self._topic)

        # Push to directory if available
        if self._directory:
            await self._directory.push_agent_record(self._managed_object.agent_card)

        # Protocol-level setup (ASGI bridge, tracing, etc.)
        await self._protocol.setup()

        logger.info(f"A2A patterns handler started on topic: {self._topic}")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.info("A2A transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing A2A transport: {e}")
