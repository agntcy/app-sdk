# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, Type
from enum import Enum
import os

from agntcy_app_sdk.transports.base import BaseTransport
from agntcy_app_sdk.transports.slim.transport import SLIMTransport
from agntcy_app_sdk.transports.nats.transport import NatsTransport
from agntcy_app_sdk.transports.streamable_http.transport import StreamableHTTPTransport

from agntcy_app_sdk.protocols.base import BaseAgentProtocolHandler
from agntcy_app_sdk.protocols.a2a.protocol import A2AProtocol
from agntcy_app_sdk.protocols.mcp.protocol import MCPProtocol
from agntcy_app_sdk.protocols.fast_mcp.protocol import FastMCPProtocol

from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.directory.dir.agent_directory import AgntcyAgentDirectory
from agntcy_app_sdk.directory.git.agent_directory import GitAgentDirectory

from a2a.server.apps import A2AStarletteApplication
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.fastmcp import FastMCP

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


# a utility enum class to define observability providers as constants
class ObservabilityProviders(Enum):
    IOA_OBSERVE = "ioa_observe"


# a utility enum class to define identity providers as constants
class IdentityProviders(Enum):
    AGNTCY = "agntcy_identity"


# a utility enum class to define agent directory backends as constants
class AgentDirectories(Enum):
    GIT = "git"
    AGNTCY_DIR = "dir"


class AgntcyFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """

    def __init__(
        self,
        name="AgntcyFactory",
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
        self._protocol_registry: Dict[str, Type[BaseAgentProtocolHandler]] = {}
        self._directory_registry: Dict[str, Type[BaseAgentDirectory]] = {}

        self._clients = {}
        self._bridges = {}

        self._register_wellknown_transports()
        self._register_wellknown_protocols()
        self._register_wellknown_directories()

        if self.enable_tracing:
            os.environ["TRACING_ENABLED"] = "true"
            from ioa_observe.sdk import Observe

            Observe.init(
                self.name,
                api_endpoint=os.getenv("OTLP_HTTP_ENDPOINT", "http://localhost:4318"),
            )

            logger.info(f"Tracing enabled for {self.name} via ioa_observe.sdk")

    def registered_protocols(self):
        """
        Get the list of registered protocol types.
        """
        return list(self._protocol_registry.keys())

    def registered_transports(self):
        """
        Get the list of registered transport types.
        """
        return list(self._transport_registry.keys())

    def registered_observability_providers(self):
        """
        Get the list of registered observability providers.
        """
        return [provider.value for provider in ObservabilityProviders]

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
        server,
        transport: BaseTransport,
        topic: str | None = None,
        agent_directory: BaseAgentDirectory | None = None,
    ) -> MessageBridge:
        """
        Create a bridge/receiver for the specified transport and protocol.
        """

        if isinstance(server, A2AStarletteApplication):
            if topic is None:
                topic = A2AProtocol.create_agent_topic(server.agent_card)
            handler = self.create_protocol("A2A")
            handler.bind_server(server)
        elif isinstance(server, MCPServer):
            if topic is None:
                raise ValueError("Topic must be provided for MCP server")
            logger.info(f"Creating MCP bridge for topic: {topic}")
            handler = self.create_protocol("MCP")
            handler.bind_server(server)

        elif isinstance(server, FastMCP):
            if topic is None:
                raise ValueError("Topic must be provided for FastMCP server")
            logger.info(f"Creating FastMCP bridge for topic: {topic}")
            handler = self.create_protocol("FastMCP")
            handler.bind_server(server)

        else:
            raise ValueError("Unsupported server type")

        bridge = MessageBridge(
            transport=transport,
            protocol_handler=handler,
            topic=topic,
            agent_directory=agent_directory,
        )

        self._bridges[topic] = bridge

        return bridge

    def create_transport(
        self, transport: str, name=None, client=None, endpoint: str = None, **kwargs
    ):
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
            transport = gateway_class.from_client(client, name=name, **kwargs)
        else:
            transport = gateway_class.from_config(endpoint, name=name, **kwargs)

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

    def create_directory(
        self, directory_type: str, *args, **kwargs
    ) -> BaseAgentDirectory:
        """
        Get a directory implementation given the directory_type.
        """
        directory_class = self._directory_registry.get(directory_type)
        if directory_class is None:
            logger.warning(
                f"No directory registered for directory type: {directory_type}"
            )
            return None
        return directory_class(*args, **kwargs)

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

        def decorator(protocol_class: Type[BaseAgentProtocolHandler]):
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
        self._protocol_registry["FastMCP"] = FastMCPProtocol

    def _register_wellknown_directories(self):
        """
        Register well-known agent directories. New directories can be registered using the register decorator.
        """
        self._directory_registry["GIT"] = GitAgentDirectory
        self._directory_registry["DIR"] = AgntcyAgentDirectory
