# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import dataclasses
import datetime
import os
import warnings
from typing import Any

import httpx
from a2a.client import A2ACardResolver
from a2a.client.base_client import BaseClient
from a2a.client.client import Client
from a2a.client.client_factory import ClientFactory as UpstreamClientFactory
from a2a.client.interceptors import ClientCallInterceptor
from a2a.types import AgentCard, AgentInterface

from slima2a.client_transport import SRPCTransport

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import wire_binding
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

    1. The ``AgentCard`` declares the server's available transports in
       ``supported_interfaces`` (list order = server preference).
    2. The ``ClientConfig.supported_protocol_bindings`` (auto-derived from
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
        ``supported_interfaces`` and the client's configured capabilities.
        For transports that require async setup (SLIM, NATS patterns), the
        transport is constructed and ``await``-ed here.  For sync
        transports (JSONRPC, gRPC, slimrpc), the upstream
        ``ClientFactory`` handles construction.

        Args:
            card: An ``AgentCard`` defining the remote agent.
            consumers: **Deprecated and ignored.**  a2a-sdk 1.x removed
                client consumers; iterate the events returned by
                ``Client.send_message()`` instead.
            interceptors: Optional list of request interceptors.

        Returns:
            A ``Client`` instance.  For patterns transports this is an
            ``A2AExperimentalClient``; for sync transports (JSONRPC,
            slimrpc) it is the upstream ``Client`` (``BaseClient``).
        """
        if consumers:
            warnings.warn(
                "The 'consumers' argument is deprecated and ignored: "
                "a2a-sdk 1.x removed client consumers.  Handle the events "
                "yielded by Client.send_message() instead.",
                DeprecationWarning,
                stacklevel=2,
            )

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
                interceptors or [],
            )
            return A2AExperimentalClient(
                client=upstream_client,
                agent_card=card,
                transport=base_transport,
                topic=topic,
                interceptors=interceptors,
            )
        elif transport_label_lower == "slimrpc":
            # Deferred slimrpc — lazily build the channel factory from
            # SlimRpcConfig if an eager factory was not provided.
            await self._build_slimrpc_if_needed()
            # slima2a's channel factory expects a bare "org/ns/name"
            # identity, but cards may use slim:// URLs for consistency
            # with other transports.  Hand the upstream factory a card
            # holding only the chosen interface, with a bare identity.
            selected = self._select_card(
                card, transport_label_lower, transport_url, url=topic
            )
            return self._upstream.create(selected, interceptors)
        else:
            # Sync path — construct JSONRPC client via upstream factory.
            # The upstream factory matches ``protocol_binding`` exactly
            # (``"JSONRPC"``), so hand it a card holding only the chosen
            # interface with the binding rewritten to the upstream casing.
            selected = self._select_card(card, transport_label_lower, transport_url)
            return self._upstream.create(selected, interceptors)

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
            consumers: **Deprecated and ignored** (see :meth:`create`).
            interceptors: Optional list of request interceptors.

        Returns:
            A ``Client`` instance.
        """
        if isinstance(agent, str):
            async with httpx.AsyncClient() as http_client:
                resolver = A2ACardResolver(http_client, base_url=agent)
                card = await resolver.get_agent_card()
            # A card without any interface cannot be negotiated; assume it
            # is served over JSON-RPC at the URL it was fetched from.
            if not card.supported_interfaces:
                card.supported_interfaces.append(
                    AgentInterface(protocol_binding="JSONRPC", url=agent)
                )
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
        logic over ``card.supported_interfaces``.  By default, server
        preference (list order) wins unless ``use_client_preference`` is set
        on the config.

        Returns:
            A ``(transport_label, url)`` tuple.

        Raises:
            ValueError: If no compatible transport is found.
        """
        server_set: list[tuple[str, str]] = [
            (iface.protocol_binding, iface.url)
            for iface in card.supported_interfaces
            if iface.protocol_binding
        ]
        client_set = self._config.supported_protocol_bindings or ["JSONRPC"]

        # Case-insensitive comparison that also resolves aliases
        # (e.g. "slim" -> "slimpatterns") so that server and client
        # transport identifiers always match on canonical names.
        client_lower = {normalize_transport(c) for c in client_set}

        transport_protocol: str | None = None
        transport_url: str | None = None

        if self._config.use_client_preference:
            for cl in client_set:
                wanted = normalize_transport(cl)
                match = next(
                    (
                        (label, url)
                        for label, url in server_set
                        if normalize_transport(label) == wanted
                    ),
                    None,
                )
                if match is not None:
                    transport_protocol, transport_url = match
                    break
        else:
            for label, url in server_set:
                if normalize_transport(label) in client_lower:
                    transport_protocol, transport_url = label, url
                    break

        if transport_protocol is None or transport_url is None:
            hint = ""
            if not server_set:
                hint = (
                    " The agent card declares no supported_interfaces; add an "
                    "AgentInterface for the transport to use, or call "
                    "A2AClientFactory.connect(url) to resolve the card from "
                    "its URL (assumes JSONRPC at that URL)."
                )
            raise ValueError(
                f"No compatible transports. "
                f"Server offers {[label for label, _ in server_set]}, "
                f"client supports {list(client_set)}.{hint}"
            )

        return transport_protocol, transport_url

    @staticmethod
    def _select_card(
        card: AgentCard,
        label: str,
        transport_url: str,
        *,
        url: str | None = None,
    ) -> AgentCard:
        """Return a copy of *card* holding only the negotiated interface.

        The upstream factory matches ``protocol_binding`` exactly, so the
        binding is rewritten to the casing it expects (``"JSONRPC"``).
        *url* optionally overrides the interface URL (used to hand slimrpc
        a bare ``org/ns/name`` identity).  The caller's card is not mutated.
        """
        selected = AgentCard()
        selected.CopyFrom(card)
        original = next(
            (
                i
                for i in card.supported_interfaces
                if i.url == transport_url
                and normalize_transport(i.protocol_binding) == label
            ),
            None,
        )
        iface = AgentInterface()
        if original is not None:
            iface.CopyFrom(original)
        iface.protocol_binding = wire_binding(label)
        iface.url = url if url is not None else transport_url
        del selected.supported_interfaces[:]
        selected.supported_interfaces.append(iface)
        return selected

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
             ``get_or_init_slim_service()`` — idempotent if slimpatterns
             already ran.  No pub/sub App is created: it is not needed for
             RPC and SLIM 2.x does not cope with it next to the RPC App.
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

        from agntcy_app_sdk.transport.slim.common import get_or_init_slim_service
        from slima2a.client_transport import (
            slimrpc_channel_factory as _slimrpc_channel_factory,
        )

        rpc_cfg = config.slimrpc_config

        # The Rust→Python callbacks need to know which loop to resume on.
        slim_bindings.uniffi_set_event_loop(asyncio.get_running_loop())

        # 1) Ensure the global SLIM runtime is initialised and take the
        #    service.  If slimpatterns already did this it is a no-op.
        service = get_or_init_slim_service()

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
