# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
import datetime
import os
from typing import Any

import httpx
from a2a.client import A2ACardResolver
from a2a.client.base_client import BaseClient
from a2a.client.client import Client
from a2a.client.client_factory import ClientFactory as UpstreamClientFactory
from a2a.client.middleware import ClientCallInterceptor
from a2a.types import AgentCapabilities, AgentCard

from slima2a.client_transport import SRPCTransport

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.additional_patterns import (
    A2AExperimentalClient,
)
from agntcy_app_sdk.semantic.a2a.client.transports import (
    PatternsClientTransport,
    _parse_topic_from_url,
)
from agntcy_app_sdk.semantic.base import ClientFactory
from agntcy_app_sdk.transport.base import BaseTransport

configure_logging()
logger = get_logger(__name__)


class A2AClientFactory(ClientFactory):
    """Card-driven A2A client factory.

    Constructed with a :class:`ClientConfig` declaring the transports
    the client is capable of using (deferred configs and/or pre-built
    instances).  Reusable — call :meth:`create` for each agent you want
    to connect to.

    Transport negotiation follows the upstream A2A pattern:

    1. The ``AgentCard`` declares the server's available transports
       (``preferred_transport``, ``additional_interfaces``).
    2. The ``ClientConfig.supported_transports`` (auto-derived from
       configured fields) declares the client's capabilities.
    3. :meth:`create` finds the best intersection and lazily
       constructs the winning transport — including async setup.

    Example::

        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357",
                name="agntcy/demo/client",
            ),
        )
        factory = A2AClientFactory(config)
        client = await factory.create(card)
    """

    def __init__(
        self,
        config: ClientConfig | None = None,
    ):
        self._config = config or ClientConfig()
        self._upstream = UpstreamClientFactory(self._config)
        self._register_transports()

    # ------------------------------------------------------------------
    # ClientFactory ABC
    # ------------------------------------------------------------------

    def protocol_type(self) -> str:
        """Return the protocol type identifier."""
        return "A2A"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create(
        self,
        card: AgentCard,
        consumers: list[Any] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> Client:
        """Create a client for the given AgentCard.

        Negotiates the best transport match between the card's declared
        transports and the client's configured capabilities.  For
        transports that require async setup (SLIM, NATS patterns), the
        transport is constructed and ``await``-ed here.  For sync
        transports (JSONRPC, gRPC, slimrpc), the upstream
        ``ClientFactory`` handles construction.

        Args:
            card: An ``AgentCard`` defining the remote agent.
            consumers: Optional list of consumer callbacks.
            interceptors: Optional list of request interceptors.

        Returns:
            A ``Client`` instance.  For patterns transports this is an
            ``A2AExperimentalClient``; for sync transports (JSONRPC,
            slimrpc) it is the upstream ``Client`` (``BaseClient``).
        """
        self._initialize_tracing_if_enabled()

        transport_label, transport_url = self._negotiate(card)
        topic = _parse_topic_from_url(transport_url)

        if transport_label in ("slimpatterns", "natspatterns"):
            # Async path — we build the transport ourselves because
            # upstream ClientFactory.create() is sync and cannot call
            # await transport.setup().
            base_transport = await self._build_patterns_transport(transport_label)
            patterns_transport = PatternsClientTransport(base_transport, card, topic)
            upstream_client = BaseClient(
                card,
                self._config,
                patterns_transport,
                consumers or [],
                interceptors or [],
            )
            return A2AExperimentalClient(
                client=upstream_client,
                agent_card=card,
                transport=base_transport,
                topic=topic,
            )
        else:
            # Sync path — delegate to upstream for JSONRPC, slimrpc, etc.
            return self._upstream.create(card, consumers, interceptors)

    async def create_client(
        self,
        *,
        url: str | None = None,
        topic: str | None = None,
        transport: BaseTransport | None = None,
        **kwargs: Any,
    ) -> Client:
        """Bridge for ``AgntcyFactory.create_client()``.

        Translates the legacy ``(url, topic, transport)`` calling convention
        into the card-driven ``create(card)`` API:

        * If *url* points to an HTTP endpoint the AgentCard is fetched from
          the well-known ``/.well-known/agent.json`` path.
        * If only *topic* + *transport* are provided, a minimal AgentCard is
          synthesised so that ``create()`` can select the correct transport.
        * A pre-built *transport* is injected into the factory's config as an
          eager transport so ``_build_patterns_transport()`` can return it.
        """
        # Inject eager transport into our config so create() can use it
        if transport is not None:
            ttype = transport.type()
            if ttype == "SLIM" and self._config.slim_transport is None:
                self._config.slim_transport = transport
            elif ttype == "NATS" and self._config.nats_transport is None:
                self._config.nats_transport = transport
            # Re-derive supported_transports after injection
            self._config.supported_transports = []
            self._config.__post_init__()

            # Ensure the transport is connected
            await transport.setup()

        # Resolve or synthesise an AgentCard
        card: AgentCard
        if url and url.startswith("http"):
            try:
                async with httpx.AsyncClient() as http_client:
                    resolver = A2ACardResolver(http_client, base_url=url)
                    card = await resolver.get_agent_card()
                # If the card has an empty url, backfill with the fetch URL
                if not card.url:
                    card.url = url
            except Exception:
                logger.warning(
                    "Could not resolve AgentCard from %s, synthesising one", url
                )
                card = self._synthesise_card(url, topic, transport)
        else:
            card = self._synthesise_card(url, topic, transport)

        return await self.create(card, **kwargs)

    @staticmethod
    def _synthesise_card(
        url: str | None,
        topic: str | None,
        transport: BaseTransport | None,
    ) -> AgentCard:
        """Build a minimal ``AgentCard`` from topic and transport info."""
        # Map transport type → (preferred_transport, URI scheme)
        _scheme_map: dict[str, tuple[str, str]] = {
            "SLIM": ("slimpatterns", "slim"),
            "NATS": ("natspatterns", "nats"),
        }

        preferred = "JSONRPC"
        card_url = url or "http://localhost"

        if transport is not None and topic:
            entry = _scheme_map.get(transport.type())
            if entry:
                preferred, scheme = entry
                card_url = f"{scheme}://{topic}"

        return AgentCard(
            name=topic or "unknown",
            description="Auto-synthesised card",
            url=card_url,
            version="0.0.0",
            defaultInputModes=["text"],
            defaultOutputModes=["text"],
            capabilities=AgentCapabilities(),
            skills=[],
            preferredTransport=preferred,
        )

    @classmethod
    async def connect(
        cls,
        agent: str | AgentCard,
        config: ClientConfig | None = None,
        consumers: list[Any] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> Client:
        """Convenience: resolve a card from a URL and create a client.

        If ``agent`` is a string, it is treated as the base URL of the
        remote agent and the card is fetched from the well-known path.
        If ``agent`` is already an ``AgentCard``, it is used directly.

        Args:
            agent: Base URL string or an ``AgentCard``.
            config: Optional ``ClientConfig``.
            consumers: Optional list of consumer callbacks.
            interceptors: Optional list of request interceptors.

        Returns:
            A ``Client`` instance.
        """
        if isinstance(agent, str):
            async with httpx.AsyncClient() as http_client:
                resolver = A2ACardResolver(http_client, base_url=agent)
                card = await resolver.get_agent_card()
        else:
            card = agent

        config = config or ClientConfig()
        factory = cls(config)
        return await factory.create(card, consumers, interceptors)

    # ------------------------------------------------------------------
    # Transport negotiation
    # ------------------------------------------------------------------

    def _negotiate(self, card: AgentCard) -> tuple[str, str]:
        """Find the best matching transport between card and client config.

        Replicates the upstream ``ClientFactory.create()`` negotiation
        logic.  By default, server preference wins unless
        ``use_client_preference`` is set on the config.

        Returns:
            A ``(transport_label, url)`` tuple.

        Raises:
            ValueError: If no compatible transport is found.
        """
        server_preferred = card.preferred_transport or "JSONRPC"
        server_set: dict[str, str] = {server_preferred: card.url}
        if card.additional_interfaces:
            server_set.update({x.transport: x.url for x in card.additional_interfaces})

        client_set = self._config.supported_transports or ["JSONRPC"]

        transport_protocol: str | None = None
        transport_url: str | None = None

        if self._config.use_client_preference:
            for x in client_set:
                if x in server_set:
                    transport_protocol = x
                    transport_url = server_set[x]
                    break
        else:
            for x, url in server_set.items():
                if x in client_set:
                    transport_protocol = x
                    transport_url = url
                    break

        if transport_protocol is None or transport_url is None:
            raise ValueError(
                f"No compatible transports. "
                f"Server offers {list(server_set)}, "
                f"client supports {list(client_set)}."
            )

        return transport_protocol, transport_url

    # ------------------------------------------------------------------
    # Async transport construction (deferred path)
    # ------------------------------------------------------------------

    async def _build_patterns_transport(self, label: str) -> BaseTransport:
        """Lazily construct and set up a patterns transport.

        Checks for a pre-built (eager) transport first.  Falls back to
        constructing one from the typed config (deferred) and calling
        ``await transport.setup()``.
        """
        config = self._config

        if label == "slimpatterns":
            if config.slim_transport is not None:
                return config.slim_transport

            if config.slim_config is not None:
                from agntcy_app_sdk.transport.slim.transport import SLIMTransport

                # Forward all optional fields as **kwargs so SLIMTransport
                # picks up security, timeout, and retry settings.
                slim_kwargs = {
                    k: v
                    for k, v in dataclasses.asdict(config.slim_config).items()
                    if k not in ("endpoint", "name", "message_timeout_seconds")
                    and v is not None
                }
                # Convert seconds → timedelta for SLIMTransport.__init__()
                slim_kwargs["message_timeout"] = datetime.timedelta(
                    seconds=config.slim_config.message_timeout_seconds,
                )
                transport = SLIMTransport.from_config(
                    config.slim_config.endpoint,
                    name=config.slim_config.name,
                    **slim_kwargs,
                )
                await transport.setup()
                return transport

            raise ValueError(
                "Card selected 'slimpatterns' but neither slim_transport "
                "nor slim_config is set on ClientConfig."
            )

        if label == "natspatterns":
            if config.nats_transport is not None:
                return config.nats_transport

            if config.nats_config is not None:
                from agntcy_app_sdk.transport.nats.transport import NatsTransport

                # Forward all optional fields as **kwargs so NatsTransport
                # picks up connection and timeout settings.
                nats_kwargs = {
                    k: v
                    for k, v in dataclasses.asdict(config.nats_config).items()
                    if k not in ("endpoint",) and v is not None
                }
                transport = NatsTransport.from_config(
                    config.nats_config.endpoint,
                    **nats_kwargs,
                )
                await transport.setup()
                return transport

            raise ValueError(
                "Card selected 'natspatterns' but neither nats_transport "
                "nor nats_config is set on ClientConfig."
            )

        raise ValueError(f"Unknown patterns transport label: {label!r}")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _register_transports(self) -> None:
        """Register SDK transport producers with the upstream factory.

        This covers the **sync** path (upstream ``ClientFactory.create()``
        invokes ``TransportProducer`` callables synchronously).  For
        patterns transports, the sync producer can only work with
        pre-built (eager) transports on the config.  The deferred
        (async) path is handled by ``_build_patterns_transport()``.
        """
        config = self._config

        # SLIM-RPC (slima2a protobuf-over-SLIM)
        if config.slimrpc_channel_factory is not None:
            self._upstream.register("slimrpc", SRPCTransport.create)
            logger.debug("Registered slimrpc transport")

        # SLIM patterns — register for sync fallback (requires eager transport)
        if config.slim_transport is not None:
            self._upstream.register("slimpatterns", PatternsClientTransport.create)
            logger.debug("Registered slimpatterns transport (eager)")

        # NATS patterns — register for sync fallback (requires eager transport)
        if config.nats_transport is not None:
            self._upstream.register("natspatterns", PatternsClientTransport.create)
            logger.debug("Registered natspatterns transport (eager)")

    def _initialize_tracing_if_enabled(self) -> None:
        """Initialize OpenTelemetry tracing if enabled."""
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            try:
                from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

                A2AInstrumentor().instrument()
                logger.info("A2A Instrumentor enabled for tracing")
            except ImportError:
                logger.warning("Tracing enabled but ioa_observe not installed")
