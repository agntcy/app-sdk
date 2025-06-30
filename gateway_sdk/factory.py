# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, Type
import os
from enum import Enum

from gateway_sdk.transports.transport import BaseTransport
from gateway_sdk.protocols.protocol import BaseAgentProtocol

from gateway_sdk.transports.slim.gateway import SLIMGateway
from gateway_sdk.transports.nats.gateway import NatsGateway
from gateway_sdk.transports.streamable_http.gateway import StreamableHTTPGateway

from gateway_sdk.protocols.a2a.gateway import A2AProtocol
from gateway_sdk.protocols.mcp.gateway import MCPProtocol
from a2a.server.apps import A2AStarletteApplication

from gateway_sdk.bridge import MessageBridge

from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.common.tracing import init_tracing

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
    StreamableHTTP = "StreamableHTTP"


class GatewayFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """

    def __init__(self, enable_logging: bool = True, enable_tracing: bool = False):
        self.enable_logging = enable_logging
        self.enable_tracing = enable_tracing

        self._transport_registry: Dict[str, Type[BaseTransport]] = {}
        self._protocol_registry: Dict[str, Type[BaseAgentProtocol]] = {}

        self._clients = {}
        self._bridges = {}

        self._register_wellknown_transports()
        self._register_wellknown_protocols()

        if self.enable_tracing:
            os.environ["TRACING_ENABLED"] = "true"
            logger.info("Tracing enabled for gateway_sdk")

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

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            init_tracing(
                app_name="gateway_client",
                collector_url=os.environ.get("COLLECTOR_URL", "http://localhost:4318"),
                disable_batch=True,
            )

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
    ) -> MessageBridge:
        """
        Create a bridge/receiver for the specified transport and protocol.
        """

        bridge = None
        topic = None

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            init_tracing(
                app_name="gateway_bridge",
                collector_url=os.environ.get("COLLECTOR_URL", "http://localhost:4318"),
                disable_batch=True,
            )

        # TODO: handle multiple server types and or agent frameworks ie graph
        if isinstance(server, A2AStarletteApplication):
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
            logger.warning(f"No gateway registered for transport type: {transport}")
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
        self._transport_registry["SLIM"] = SLIMGateway
        self._transport_registry["NATS"] = NatsGateway
        self._transport_registry["StreamableHTTP"] = StreamableHTTPGateway

    def _register_wellknown_protocols(self):
        """
        Register well-known protocols. New protocols can be registered using the register decorator.
        """
        self._protocol_registry["A2A"] = A2AProtocol
        self._protocol_registry["MCP"] = MCPProtocol
