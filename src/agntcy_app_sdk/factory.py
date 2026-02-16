# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
from typing import Any, Dict, Protocol, Type

from agntcy_app_sdk.app_sessions import AppSession
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.client_factory_base import BaseClientFactory
from agntcy_app_sdk.transport.base import BaseTransport

from agntcy_app_sdk.transport.nats.transport import NatsTransport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport
from agntcy_app_sdk.transport.streamable_http.transport import StreamableHTTPTransport

from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.fast_mcp.client_factory import FastMCPClientFactory
from agntcy_app_sdk.semantic.mcp.client_factory import MCPClientFactory

configure_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Type stubs for the dynamically-attached accessors.
# These are consumed by type checkers / IDE auto-complete only; at runtime
# the real callables are bound by ``_register_wellknown_protocols``.
# ---------------------------------------------------------------------------


class A2AAccessor(Protocol):
    def __call__(self, config: ClientConfig | None = None) -> A2AClientFactory:
        ...


class MCPAccessor(Protocol):
    def __call__(self) -> MCPClientFactory:
        ...


class FastMCPAccessor(Protocol):
    def __call__(self) -> FastMCPClientFactory:
        ...


class AgntcyFactory:
    """Unified factory interface for building interoperable multi-agent systems.

    Creates typed protocol clients (A2A, MCP, FastMCP), transports (SLIM, NATS, HTTP),
    and app sessions. Protocol accessors are dynamically generated from the registry
    during initialization, providing a clean typed API for agent communication.

    **Quick Start**::

        factory = AgntcyFactory()

        # MCP client
        async with await factory.mcp().create_client(
            topic="mcp/agent",
            transport=slim_transport
        ) as session:
            tools = await session.list_tools()

        # A2A client
        config = ClientConfig(slim_config=SlimTransportConfig(...))
        client = await factory.a2a(config).create(agent_card)
        response = await client.task("Hello, agent!")

        # FastMCP client
        client = await factory.fast_mcp().create_client(
            topic="fast-mcp/agent",
            transport=nats_transport
        )

    **Architecture**:

    - **Transports** — Message delivery layer (SLIM, NATS, HTTP)
    - **Protocols** — Semantic layer (A2A, MCP, FastMCP)
    - **Observability** — Optional distributed tracing via ``enable_tracing=True``

    The factory maintains registries of available transports and protocols,
    enabling runtime introspection (``registered_protocols()``,
    ``registered_transports()``).

    Args:
        name: Factory instance name (used for tracing service name).
        enable_tracing: Enable OpenTelemetry tracing via ioa_observe.sdk.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR).
    """

    OBSERVABILITY_PROVIDERS: list[str] = ["ioa_observe"]
    """Known observability provider identifiers."""

    # Annotate the dynamically-attached accessors so type checkers and
    # IDE auto-complete recognise them on ``AgntcyFactory`` instances.
    a2a: A2AAccessor
    mcp: MCPAccessor
    fast_mcp: FastMCPAccessor

    def __init__(
        self,
        name: str = "AgntcyFactory",
        enable_tracing: bool = False,
        log_level: str = "INFO",
    ):
        self.name = name
        self.enable_tracing = enable_tracing

        # Configure logging
        self.log_level = log_level
        try:
            logger.setLevel(log_level.upper())
        except ValueError:
            logger.error(f"Invalid log level '{log_level}'. Defaulting to INFO.")
            self.log_level = "INFO"
            logger.setLevel(self.log_level)

        self._transport_registry: Dict[str, Type[BaseTransport]] = {}
        self._protocol_registry: Dict[str, type] = {}

        self._register_wellknown_transports()
        self._register_wellknown_protocols()

        if self.enable_tracing:
            self._setup_tracing()

    # ------------------------------------------------------------------
    # Tracing
    # ------------------------------------------------------------------

    def _setup_tracing(self) -> None:
        """Initialize distributed tracing via ioa_observe.sdk / OpenTelemetry."""
        os.environ["TRACING_ENABLED"] = "true"
        from ioa_observe.sdk import Observe

        Observe.init(
            self.name,
            api_endpoint=os.getenv("OTLP_HTTP_ENDPOINT", "http://localhost:4318"),
        )

        logger.info(f"Tracing enabled for {self.name} via ioa_observe.sdk")

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def registered_protocols(self) -> list[str]:
        """Get the list of registered protocol types."""
        return list(self._protocol_registry.keys())

    def registered_transports(self) -> list[str]:
        """Get the list of registered transport types."""
        return list(self._transport_registry.keys())

    def registered_observability_providers(self) -> list[str]:
        """Get the list of registered observability providers."""
        return list(self.OBSERVABILITY_PROVIDERS)

    # ------------------------------------------------------------------
    # Transport & session creation
    # ------------------------------------------------------------------

    def create_app_session(self, max_sessions: int = 10) -> AppSession:
        """Create an app session to manage multiple app containers."""
        return AppSession(max_sessions=max_sessions)

    def create_transport(
        self,
        transport: str,
        name: str | None = None,
        client: Any = None,
        endpoint: str | None = None,
        **kwargs: Any,
    ) -> BaseTransport:
        """Create and return a transport instance for the specified transport type.

        Raises:
            ValueError: If neither ``client`` nor ``endpoint`` is provided,
                or if the requested transport type is not registered.
        """
        if not client and not endpoint:
            raise ValueError("Either client or endpoint must be provided")

        transport_class = self._transport_registry.get(transport)
        if transport_class is None:
            raise ValueError(
                f"No transport registered for transport type: {transport!r}. "
                f"Available transports: {list(self._transport_registry.keys())}"
            )

        # Build optional kwargs — only pass ``name`` when the caller supplied one
        # so that the transport's own default (``name: str = None``) is respected.
        name_kwargs: dict[str, str] = {"name": name} if name is not None else {}

        if client:
            transport_instance = transport_class.from_client(
                client, **name_kwargs, **kwargs
            )
        else:
            assert endpoint is not None  # guaranteed by the guard above
            transport_instance = transport_class.from_config(
                endpoint, **name_kwargs, **kwargs
            )

        return transport_instance

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_wellknown_transports(self) -> None:
        """Register well-known transports.

        Each entry derives its registry key from the transport class's
        ``TRANSPORT_TYPE`` constant — the label lives on the class itself.
        """
        for transport_class in (SLIMTransport, NatsTransport, StreamableHTTPTransport):
            self._transport_registry[transport_class.TRANSPORT_TYPE] = transport_class

    def _register_wellknown_protocols(self) -> None:
        """Register well-known protocols and attach accessor methods.

        For each factory class, this method:
        1. Reads ``protocol_type()`` to derive the registry key.
        2. Stores ``protocol_type → factory_class`` in the registry.
        3. Attaches a convenience accessor (e.g. ``self.a2a``) whose
           name comes from the factory's ``ACCESSOR_NAME`` constant.
        """
        for factory_class in (A2AClientFactory, MCPClientFactory, FastMCPClientFactory):
            proto_name = factory_class().protocol_type()
            self._protocol_registry[proto_name] = factory_class

            # Build a closure that captures the class for the accessor
            def _make_accessor(cls: type):
                def accessor(*args: Any, **kwargs: Any) -> BaseClientFactory:
                    return cls(*args, **kwargs)

                return accessor

            setattr(self, factory_class.ACCESSOR_NAME, _make_accessor(factory_class))
