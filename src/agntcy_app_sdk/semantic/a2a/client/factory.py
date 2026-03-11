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
from a2a.types import AgentCard

from slima2a.client_transport import SRPCTransport

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
    A2AExperimentalClient,
)
from agntcy_app_sdk.semantic.a2a.client.transports import (
    PatternsClientTransport,
    _parse_topic_from_url,
)
from agntcy_app_sdk.semantic.a2a.transport_types import normalize_transport
from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)


class A2AClientFactory:
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

    ACCESSOR_NAME: str = "a2a"
    """Method name attached to :class:`AgntcyFactory` for this protocol."""

    def protocol_type(self) -> str:
        """Return the protocol label for this factory."""
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
        # Resolve aliases (e.g. "slim" -> "slimpatterns") so dispatch
        # always works against canonical transport names.
        transport_label_lower = normalize_transport(transport_label)
        topic = _parse_topic_from_url(transport_url)

        if transport_label_lower in ("slimpatterns", "natspatterns"):
            # Async path — we build the transport ourselves because
            # upstream ClientFactory.create() is sync and cannot call
            # await transport.setup().
            base_transport = await self._build_patterns_transport(transport_label_lower)
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
        elif transport_label_lower == "slimrpc":
            # Deferred slimrpc — lazily build the channel factory from
            # SlimRpcConfig if an eager factory was not provided.
            await self._build_slimrpc_if_needed()
            # slima2a's channel factory expects a bare "org/ns/name"
            # identity, but cards may use slim:// URLs for consistency
            # with other transports.  Normalise them here so the
            # upstream factory passes a plain identity string.
            self._normalise_slimrpc_urls(card)
            return self._upstream.create(card, consumers, interceptors)
        else:
            # Sync path — construct JSONRPC client via upstream factory.
            # Normalise transport identifiers to the casing the upstream
            # ``a2a.client.client_factory`` expects (``TransportProtocol``
            # enum values are UPPERCASE, e.g. ``"JSONRPC"``).  Without
            # this, a card whose ``preferredTransport`` or
            # ``additional_interfaces`` use lowercase ``"jsonrpc"``
            # (our ``InterfaceTransport.JSONRPC``) would fail the
            # upstream's exact-match negotiation.
            self._normalise_card_transport_casing(card)
            return self._upstream.create(card, consumers, interceptors)

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
            # Backfill empty card.url with the URL used to fetch the card,
            # so that transport negotiation can match against it.
            if not card.url:
                card.url = agent
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

        # Build a case-insensitive lookup that also resolves aliases
        # (e.g. "slim" -> "slimpatterns") so that server and client
        # transport identifiers always match on canonical names.
        server_lower: dict[str, tuple[str, str]] = {
            normalize_transport(k): (k, v) for k, v in server_set.items()
        }
        client_lower: dict[str, str] = {normalize_transport(c): c for c in client_set}

        transport_protocol: str | None = None
        transport_url: str | None = None

        if self._config.use_client_preference:
            for cl in client_set:
                match = server_lower.get(normalize_transport(cl))
                if match is not None:
                    transport_protocol = match[0]
                    transport_url = match[1]
                    break
        else:
            for sk, url in server_set.items():
                if normalize_transport(sk) in client_lower:
                    transport_protocol = sk
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

        Checks for a pre-built (eager) transport first — calling
        ``await transport.setup()`` to ensure it is connected.  Falls
        back to constructing one from the typed config (deferred) and
        calling ``await transport.setup()``.
        """
        config = self._config

        if label == "slimpatterns":
            if config.slim_transport is not None:
                await config.slim_transport.setup()
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
                await config.nats_transport.setup()
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

    async def _build_slimrpc_if_needed(self) -> None:
        """Lazily construct the slimrpc channel factory from :class:`SlimRpcConfig`.

        If an eager ``slimrpc_channel_factory`` is already set on the config,
        this is a no-op.  Otherwise, a dedicated SLIM connection is opened
        for slimrpc using the trailing-slash endpoint trick (mirroring the
        server-side pattern in ``A2ASRPCServerHandler``) so that slimrpc and
        slimpatterns can coexist on the same SLIM endpoint without a
        "client already connected" collision.

        Strategy (matches server-side ``srpc.py``):
          1. Initialise the global SLIM runtime via
             ``get_or_create_slim_instance()`` — idempotent if slimpatterns
             already ran.
          2. Open a *second* connection using ``endpoint + "/"`` so
             ``slim_bindings`` treats it as a distinct endpoint key.
          3. Create a separate App under ``name + "-rpc"`` to isolate
             RPC traffic from pub/sub.
          4. Build the ``slimrpc_channel_factory`` from the dedicated
             app and connection.
        """
        config = self._config

        # Already eager — nothing to do.
        if config.slimrpc_channel_factory is not None:
            return

        if config.slimrpc_config is None:
            raise ValueError(
                "Card selected 'slimrpc' but neither slimrpc_channel_factory "
                "nor slimrpc_config is set on ClientConfig."
            )

        import slim_bindings

        from agntcy_app_sdk.transport.slim.common import get_or_create_slim_instance
        from slima2a.client_transport import (
            slimrpc_channel_factory as _slimrpc_channel_factory,
        )

        rpc_cfg = config.slimrpc_config
        rpc_name = slim_bindings.Name(rpc_cfg.namespace, rpc_cfg.group, rpc_cfg.name)

        # 1) Ensure the global SLIM runtime is initialised.  If
        #    slimpatterns already did this, it returns the cached
        #    globals (no-op).  Otherwise, the first connection is
        #    opened here.
        service, _global_app, _global_conn = await get_or_create_slim_instance(
            local=rpc_name,
            slim_endpoint=rpc_cfg.slim_url,
            slim_insecure_client=True,
            shared_secret=rpc_cfg.secret,
        )

        # 2) Open a dedicated connection for slimrpc by appending a
        #    trailing slash so the SLIM service sees it as a distinct
        #    endpoint key — avoids "client already connected" when
        #    slimpatterns already holds a connection to the same host.
        rpc_endpoint = rpc_cfg.slim_url.rstrip("/") + "/"
        rpc_client_config = slim_bindings.new_insecure_client_config(rpc_endpoint)
        conn_id = await service.connect_async(rpc_client_config)

        # 3) Create a separate App under a unique name so that the
        #    SLIM dataplane does not cross-deliver pub/sub messages to
        #    the RPC channel (or vice-versa).
        rpc_app_name = slim_bindings.Name(
            rpc_cfg.namespace, rpc_cfg.group, rpc_cfg.name + "-rpc"
        )
        slim_app = service.create_app_with_secret(rpc_app_name, rpc_cfg.secret)

        # Subscribe the new app on the dedicated connection so that
        # RPC session handshakes can find this participant.
        await slim_app.subscribe_async(rpc_app_name, conn_id)

        # 4) Build the channel factory from the dedicated app + connection.
        config.slimrpc_channel_factory = _slimrpc_channel_factory(slim_app, conn_id)
        self._upstream.register("slimrpc", SRPCTransport.create)
        logger.debug("Registered slimrpc transport (deferred from SlimRpcConfig)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # Map lowercase transport strings to their upstream TransportProtocol
    # enum values.  Only covers identifiers that the upstream SDK knows
    # about — custom SDK-only labels (slimpatterns, natspatterns, slimrpc)
    # are handled before reaching the upstream factory.
    _UPSTREAM_TRANSPORT_CASING: dict[str, str] = {
        "jsonrpc": "JSONRPC",
        "grpc": "GRPC",
        "http+json": "HTTP+JSON",
    }

    @staticmethod
    def _normalise_card_transport_casing(card: AgentCard) -> None:
        """Normalise transport identifiers on a card for upstream consumption.

        The upstream ``a2a.client.client_factory.ClientFactory`` uses
        ``TransportProtocol`` enum values (e.g. ``"JSONRPC"``) for
        matching.  Our SDK and ``InterfaceTransport`` constants use
        lowercase (``"jsonrpc"``).  This helper rewrites the card
        in-place so the upstream negotiation succeeds.
        """
        mapping = A2AClientFactory._UPSTREAM_TRANSPORT_CASING
        if card.preferred_transport:
            canonical = mapping.get(card.preferred_transport.lower())
            if canonical:
                card.preferred_transport = canonical
        if card.additional_interfaces:
            for iface in card.additional_interfaces:
                canonical = mapping.get(iface.transport.lower())
                if canonical:
                    iface.transport = canonical

    @staticmethod
    def _normalise_slimrpc_urls(card: AgentCard) -> None:
        """Rewrite slim:// URLs on slimrpc interfaces to bare identities.

        Cards may declare slimrpc interfaces with full ``slim://`` URLs
        (e.g. ``slim://host:46357/org/ns/agent``) for consistency with
        other transports.  The upstream ``SRPCTransport`` / ``slima2a``
        channel factory expects a bare ``org/ns/name`` identity string.

        This helper normalises ``card.url`` and matching
        ``additional_interfaces`` entries in-place so the upstream
        factory receives the correct format.
        """
        card.url = _parse_topic_from_url(card.url)
        if card.additional_interfaces:
            for iface in card.additional_interfaces:
                if iface.transport.lower() == "slimrpc":
                    iface.url = _parse_topic_from_url(iface.url)

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
                logger.debug("A2A Instrumentor enabled for tracing")
            except ImportError:
                logger.warning("Tracing enabled but ioa_observe not installed")
