# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from a2a.client import A2ACardResolver
from a2a.client.client import Client
from a2a.client.client_factory import ClientFactory as UpstreamClientFactory
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.enhanced_client import A2AEnhancedClient
from agntcy_app_sdk.semantic.a2a.client.transports import (
    PatternsClientTransport,
    _parse_topic_from_url,
)
from agntcy_app_sdk.semantic.a2a.client.utils import get_client_from_agent_card_topic
from agntcy_app_sdk.semantic.base import ClientFactory
from agntcy_app_sdk.transport.base import BaseTransport

configure_logging()
logger = get_logger(__name__)


class A2AClientFactory(ClientFactory):
    """Client factory for the A2A protocol.

    Integrates with the upstream ``a2a.client.ClientFactory`` for transport
    negotiation and creates ``A2AEnhancedClient`` instances that combine
    standard A2A operations (via the upstream ``Client``) with experimental
    patterns (broadcast, groupchat) via ``BaseTransport``.

    Supports two paths:

    1. **New path** — ``config=ClientConfig.from_card(card)`` with factory
       callables populated.  Transport negotiation is handled by the
       upstream ``ClientFactory``.

    2. **Legacy path** — ``transport=some_base_transport`` without an
       explicit ``ClientConfig``.  The factory auto-registers the transport
       as a ``PatternsClientTransport`` and builds a ``ClientConfig`` with
       the appropriate factory callable.
    """

    def protocol_type(self) -> str:
        return "A2A"

    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        config: Optional[ClientConfig] = None,
        add_experimental_patterns: bool = True,
        **kwargs: Any,
    ) -> A2AEnhancedClient:
        """Create an A2A client.

        Args:
            url: Agent card URL (required if topic not provided).
            topic: Agent topic (required if url not provided).
            transport: A ``BaseTransport`` instance for patterns-based
                communication.  If provided and ``config`` is not, a
                ``ClientConfig`` is auto-built.
            config: An explicit ``ClientConfig`` with factory callables.
            add_experimental_patterns: Whether to wire up experimental
                transport methods on the enhanced client.
            **kwargs: Forwarded to ``ClientConfig`` construction.

        Returns:
            An ``A2AEnhancedClient`` instance.
        """
        self._initialize_tracing_if_enabled()

        if url is None and topic is None:
            raise ValueError("Either url or topic must be provided")

        # --- Setup transport if provided ---
        if transport:
            await transport.setup()

        # --- Resolve AgentCard ---
        agent_card = await self._resolve_agent_card(url, topic, transport)

        # --- Determine effective topic from card URL or fallback ---
        effective_topic = topic or _parse_topic_from_url(agent_card.url)

        # --- Build ClientConfig ---
        if config is None:
            config = self._build_config(agent_card, transport, **kwargs)

        # --- Create upstream Client via upstream ClientFactory ---
        upstream_client = self._create_upstream_client(agent_card, config, transport)

        # --- Wrap in A2AEnhancedClient ---
        enhanced = A2AEnhancedClient(
            client=upstream_client,
            agent_card=agent_card,
            transport=transport if add_experimental_patterns else None,
            topic=effective_topic if add_experimental_patterns else None,
        )

        logger.info(
            "Created A2AEnhancedClient for '%s' (transport=%s, topic=%s)",
            agent_card.name,
            transport.type() if transport else "HTTP",
            effective_topic,
        )

        return enhanced

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _initialize_tracing_if_enabled(self) -> None:
        """Initialize OpenTelemetry tracing if enabled."""
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            try:
                from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

                A2AInstrumentor().instrument()
                logger.info("A2A Instrumentor enabled for tracing")
            except ImportError:
                logger.warning("Tracing enabled but ioa_observe not installed")

    async def _resolve_agent_card(
        self,
        url: Optional[str],
        topic: Optional[str],
        transport: Optional[BaseTransport],
    ) -> AgentCard:
        """Resolve an ``AgentCard`` from URL or topic+transport."""
        if topic and transport:
            client = await get_client_from_agent_card_topic(topic, transport)
            return client.agent_card

        if url:
            async with httpx.AsyncClient() as http_client:
                resolver = A2ACardResolver(http_client, base_url=url)
                return await resolver.get_agent_card()

        raise ValueError("Cannot resolve AgentCard: need url or topic+transport")

    def _build_config(
        self,
        card: AgentCard,
        transport: Optional[BaseTransport],
        **kwargs: Any,
    ) -> ClientConfig:
        """Build a ``ClientConfig`` — from the card if transport is present,
        otherwise a minimal default for HTTP-only."""
        if transport:
            config = ClientConfig.from_card(card, **kwargs)

            # Auto-wire the transport factory based on transport type
            transport_type = transport.type().upper()
            if "SLIM" in transport_type:
                # Capture transport in a closure so the factory returns it
                config.slim_patterns_transport_factory = lambda t=transport: t
            elif "NATS" in transport_type:
                config.nats_transport_factory = lambda t=transport: t

            return config

        # HTTP-only — use upstream defaults
        return ClientConfig(**kwargs)

    def _create_upstream_client(
        self,
        card: AgentCard,
        config: ClientConfig,
        transport: Optional[BaseTransport],
    ) -> Client:
        """Create an upstream ``Client`` using ``ClientFactory``."""
        upstream_factory = UpstreamClientFactory(config)

        # Register SLIM-RPC transport if slima2a is available and config has
        # the channel factory
        if config.slimrpc_channel_factory is not None:
            try:
                from slima2a.client_transport import SRPCTransport

                upstream_factory.register("slimrpc", SRPCTransport.create)
                logger.debug("Registered slimrpc transport")
            except ImportError:
                logger.warning("slimrpc_channel_factory set but slima2a not installed")

        # Register patterns transports
        if config.slim_patterns_transport_factory is not None:
            upstream_factory.register("slimpatterns", PatternsClientTransport.create)
            logger.debug("Registered slimpatterns transport")

        if config.nats_transport_factory is not None:
            upstream_factory.register("natspatterns", PatternsClientTransport.create)
            logger.debug("Registered natspatterns transport")

        return upstream_factory.create(card)
