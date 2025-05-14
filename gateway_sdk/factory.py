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

from gateway_sdk.transports.base_transport import BaseTransport
from gateway_sdk.protocols.base_protocol import BaseAgentProtocol

from gateway_sdk.transports.agp.gateway import AGPGateway
from gateway_sdk.transports.nats.gateway import NatsGateway

from gateway_sdk.protocols.a2a.gateway import A2AProtocol
from a2a.server import A2AServer

from gateway_sdk.bridge import MessageBridge

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

    def create_client(
        self, protocol: str, 
        agent_endpoint: str,
        transport: BaseTransport = None,
        **kwargs
    ):
        """
        Create a client for the specified transport and protocol.
        """
       
        # get the protocol class
        protocol_instance = self.create_protocol(protocol)

        # create the client
        client = protocol_instance.create_client(agent_endpoint, transport)

        self._clients[agent_endpoint] = client
  
        return client

    def create_bridge(
        self,
        server, # how to we specify the type of server?
        transport: BaseTransport,
    ) -> MessageBridge:
        """
        Create a bridge/receiver for the specified transport and protocol.
        """

        bridge = None
        topic = None

        # TODO: handle multiple server types and or agent frameworks ie graph
        if isinstance(server, A2AServer):
            topic = f"{server.agent_card.name}_{server.agent_card.version}"
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

    def create_transport(self, transport: str, gateway_endpoint: str, *args, **kwargs):
        """
        Get the transport class for the specified transport type. Enables users to
        instantiate a transport class with a string name.
        """
        gateway_class = self._transport_registry.get(transport)
        if gateway_class is None:
            raise ValueError(f"No gateway registered for transport type: {transport}")
        transport = gateway_class(gateway_endpoint, *args, **kwargs)
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
        self._transport_registry["AGP"] = AGPGateway
        self._transport_registry["NATS"] = NatsGateway

    def _register_wellknown_protocols(self):
        """
        Register well-known protocols. New protocols can be registered using the register decorator.
        """
        self._protocol_registry["A2A"] = A2AProtocol
            
        
            
