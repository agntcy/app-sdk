# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, Type
from enum import Enum
import os

from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol

from agntcy_app_sdk.transports.slim.transport import SLIMTransport
from agntcy_app_sdk.transports.nats.transport import NatsTransport
from agntcy_app_sdk.transports.streamable_http.transport import StreamableHTTPTransport

from agntcy_app_sdk.protocols.a2a.protocol import A2AProtocol
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from a2a.server.apps import A2AStarletteApplication

from agntcy_app_sdk.bridge import MessageBridge

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


# a utility enum class to define transport types as constants
class ProtocolTypes(Enum):
    A2A = "A2A"
    MCP = "MCP"


# a utility enum class to define transport types as constants
class TransportTypes(Enum):
    A2A = "A2A"
    SLIM = "SLIM"
    NATS = "NATS"
    MQTT = "MQTT"
    STREAMABLE_HTTP = "StreamableHTTP"


class AgntcyFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """

    def __init__(
        self,
        name="AgntcyFactory",
        enable_tracing: bool = False,
        log_level: str = "DEBUG",
    ):
        self.name = name
        self.enable_tracing = enable_tracing

        # Configure logging
        self.log_level = log_level
        try:
            logger.setLevel(log_level.upper())
        except ValueError:
            logger.error(f"Invalid log level '{log_level}'. Defaulting to DEBUG.")
            self.log_level = "DEBUG"
            logger.setLevel(self.log_level)

        self._transport_registry: Dict[str, Type[BaseTransport]] = {}
        self._protocol_registry: Dict[str, Type[BaseAgentProtocol]] = {}

        self._clients = {}
        self._bridges = {}

        self._register_wellknown_transports()
        self._register_wellknown_protocols()

        if self.enable_tracing:
            os.environ["TRACING_ENABLED"] = "true"
            from ioa_observe.sdk import Observe

            Observe.init(
                self.name,
                api_endpoint=os.getenv("OTLP_HTTP_ENDPOINT", "http://localhost:4318"),
            )

            logger.info(f"Tracing enabled for {self.name} via ioa_observe.sdk")

    def create_client(
        self,
        protocol: str,
        agent_url: str | None = None,
        agent_topic: str | None = None,
        transport: BaseTransport | None = None,
        **kwargs,
    ):
        """
        Create a client for the specified transport and protocol.
        """

        if agent_url is None and agent_topic is None:
            raise ValueError("Either agent_url or agent_topic must be provided")

        # get the protocol class
        protocol_instance = self.create_protocol(protocol)

        # create the client
        client = protocol_instance.create_client(
            url=agent_url, topic=agent_topic, transport=transport
        )

        key = agent_url if agent_url else agent_topic
        self._clients[key] = client

        return client

    def create_bridge(
        self,
        server,  # add type hints for server, e.g., A2AStarletteApplication
        transport: BaseTransport,
        topic: str | None = None,
    ) -> MessageBridge:
        """
        Create a bridge/receiver for the specified transport and protocol.
        """

        if isinstance(server, A2AStarletteApplication):
            if topic is None:
                topic = A2AProtocol.create_agent_topic(server.agent_card)
            handler = self.create_protocol("A2A").create_ingress_handler(server)
        else:
            raise ValueError("Unsupported server type")

        bridge = MessageBridge(
            transport=transport,
            handler=handler,
            topic=topic,
        )

        self._bridges[topic] = bridge

        return bridge

    def create_transport(self, transport: str, client=None, endpoint: str = None):
        """
        Get the transport class for the specified transport type. Enables users to
        instantiate a transport class with a string name or a client instance.
        """
        if not client and not endpoint:
            raise ValueError("Either client or endpoint must be provided")

        gateway_class = self._transport_registry.get(transport)
        if gateway_class is None:
            logger.warning(f"No transport registered for transport type: {transport}")
            return None

        if client:
            # create the transport instance from the client
            transport = gateway_class.from_client(client)
        else:
            transport = gateway_class.from_config(endpoint)

        return transport

    def create_protocol(self, protocol: str):
        """
        Get the protocol class for the specified protocol type. Enables users to
        instantiate a protocol class with a string name.
        """
        protocol_class = self._protocol_registry.get(protocol)
        if protocol_class is None:
            raise ValueError(f"No protocol registered for protocol type: {protocol}")
        # create the protocol instance
        protocol_instance = protocol_class()
        return protocol_instance

    @classmethod
    def register_transport(cls, transport_type: str):
        """Decorator to register a new transport implementation."""

        def decorator(transport_class: Type[BaseTransport]):
            cls.self._transport_registry[transport_type] = transport_class
            return transport_class

        return decorator

    @classmethod
    def register_protocol(cls, protocol_type: str):
        """Decorator to register a new protocol implementation."""

        def decorator(protocol_class: Type[BaseAgentProtocol]):
            cls.self._protocol_registry[protocol_type] = protocol_class
            return protocol_class

        return decorator

    def _register_wellknown_transports(self):
        """
        Register well-known transports. New transports can be registered using the register decorator.
        """
        self._transport_registry["SLIM"] = SLIMTransport
        self._transport_registry["NATS"] = NatsTransport
        self._transport_registry["STREAMABLE_HTTP"] = StreamableHTTPTransport

    def _register_wellknown_protocols(self):
        """
        Register well-known protocols. New protocols can be registered using the register decorator.
        """
        self._protocol_registry["A2A"] = A2AProtocol
        self._protocol_registry["MCP"] = MCPProtocol
