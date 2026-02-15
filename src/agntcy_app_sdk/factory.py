# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
from enum import Enum
from typing import Any, Dict, Optional, Type

from agntcy_app_sdk.app_sessions import AppSession
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transport.base import BaseTransport

from agntcy_app_sdk.transport.nats.transport import NatsTransport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport
from agntcy_app_sdk.transport.streamable_http.transport import StreamableHTTPTransport

from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.fast_mcp.client_factory import FastMCPClientFactory
from agntcy_app_sdk.semantic.mcp.client_factory import MCPClientFactory

configure_logging()
logger = get_logger(__name__)


# a utility enum class to define transport types as constants
class ProtocolTypes(Enum):
    A2A = "A2A"
    MCP = "MCP"


# a utility enum class to define transport types as constants
class TransportTypes(Enum):
    A2A = "A2A"
    JSONRPC = "JSONRPC"
    SLIM = "SLIM"
    NATS = "NATS"
    MQTT = "MQTT"
    STREAMABLE_HTTP = "StreamableHTTP"


# a utility enum class to define observability providers as constants
class ObservabilityProviders(Enum):
    IOA_OBSERVE = "ioa_observe"


# a utility enum class to define identity providers as constants
class IdentityProviders(Enum):
    AGNTCY = "agntcy_identity"


class AgntcyFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """

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
        self._client_factory_registry: Dict[str, Any] = {}

        self._clients: Dict[str, Any] = {}
        self._bridges: Dict[str, Any] = {}

        self._register_wellknown_transports()
        self._register_wellknown_client_factories()

        if self.enable_tracing:
            os.environ["TRACING_ENABLED"] = "true"
            from ioa_observe.sdk import Observe

            Observe.init(
                self.name,
                api_endpoint=os.getenv("OTLP_HTTP_ENDPOINT", "http://localhost:4318"),
            )

            logger.info(f"Tracing enabled for {self.name} via ioa_observe.sdk")

    def registered_protocols(self) -> list[str]:
        """Get the list of registered protocol types."""
        return list(self._client_factory_registry.keys())

    def registered_transports(self) -> list[str]:
        """Get the list of registered transport types."""
        return list(self._transport_registry.keys())

    def registered_observability_providers(self) -> list[str]:
        """Get the list of registered observability providers."""
        return [provider.value for provider in ObservabilityProviders]

    async def create_client(
        self,
        protocol: str,
        agent_url: Optional[str] = None,
        agent_topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> Any:
        """Create a client for the specified protocol."""
        if agent_url is None and agent_topic is None:
            raise ValueError("Either agent_url or agent_topic must be provided")

        factory_instance = self._client_factory_registry.get(protocol)
        if factory_instance is None:
            raise ValueError(f"No client factory registered for protocol: {protocol}")

        client = await factory_instance.create_client(
            url=agent_url, topic=agent_topic, transport=transport, **kwargs
        )

        key = agent_url if agent_url else agent_topic
        self._clients[key] = client
        return client

    def create_app_session(self, max_sessions: int = 10) -> AppSession:
        """Create an app session to manage multiple app containers."""
        return AppSession(max_sessions=max_sessions)

    def create_transport(
        self,
        transport: str,
        name: Optional[str] = None,
        client: Any = None,
        endpoint: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[BaseTransport]:
        """Get the transport class for the specified transport type."""
        if not client and not endpoint:
            raise ValueError("Either client or endpoint must be provided")

        gateway_class = self._transport_registry.get(transport)
        if gateway_class is None:
            logger.warning(f"No transport registered for transport type: {transport}")
            return None

        if client:
            transport_instance = gateway_class.from_client(client, name=name, **kwargs)
        else:
            transport_instance = gateway_class.from_config(
                endpoint, name=name, **kwargs
            )

        return transport_instance

    @classmethod
    def register_transport(cls, transport_type: str):
        """Decorator to register a new transport implementation."""

        def decorator(transport_class: Type[BaseTransport]):
            cls.self._transport_registry[transport_type] = transport_class
            return transport_class

        return decorator

    def _register_wellknown_transports(self):
        """Register well-known transports."""
        self._transport_registry["SLIM"] = SLIMTransport
        self._transport_registry["NATS"] = NatsTransport
        self._transport_registry["STREAMABLE_HTTP"] = StreamableHTTPTransport

    def _register_wellknown_client_factories(self):
        """Register well-known client factories."""
        self._client_factory_registry["A2A"] = A2AClientFactory()
        self._client_factory_registry["MCP"] = MCPClientFactory()
        self._client_factory_registry["FastMCP"] = FastMCPClientFactory()
