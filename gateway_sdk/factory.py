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

from .agp.gateway import AGPGateway
from .nats.gateway import NatsGateway

from .a2a.gateway import A2AFactory

from typing import Dict, Type
from .base_transport import BaseTransport
from .base_protocol import BaseProtocol

class GatewayFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """
    def __init__(self, enable_logging: bool = True, enable_tracing: bool = False):
        self.enable_logging = enable_logging
        self.enable_tracing = enable_tracing

        self._transport_registry: Dict[str, Type[BaseTransport]] = {}
        self._protocol_registry: Dict[str, Type[BaseProtocol]] = {}

        # manage the state of the factory
        # TODO: define a state interface
        self._clients = {} 
        self._receivers = {}
        self._sessions = {}

        self._register_wellknown_transports()
        self._register_wellknown_protocols()

    def create_client(
        self, protocol: str, 
        agent_endpoint: str, 
        transport: str = None,
        gateway_endpoint: str = "",
        auth=None
    ):

        gateway = None

        if transport is not None:
            gateway_class = self._transport_registry.get(transport)
            if gateway_class is None:
                raise ValueError(f"No gateway registered for transport type: {transport}")
            gateway = gateway_class(gateway_endpoint, auth)
       
        # get the protocol class
        protocol_class = self._protocol_registry.get(protocol)
        if protocol_class is None:
            raise ValueError(f"No protocol registered for protocol type: {protocol}")
        # create the protocol instance
        protocol_instance = protocol_class()
        # create the client
        client = protocol_instance.create_client(agent_endpoint, gateway, auth)
  
        return client

    def create_receiver(
        self,
        protocol: str,
        topic: str,
        fastapi_app: any = None,
        flask_app: any = None,
        onMessage: callable = None,
        gateway_endpoint: str = "",
        transport: str = "",
        auth: any = None,
    ):
        """
        Create a receiver for the specified transport and protocol.
        """
        
        gateway = None
        receiver = None
            
        #gateway.subscribe()

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
        def decorator(protocol_class: Type[BaseProtocol]):
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
        self._protocol_registry["A2A"] = A2AFactory
            
        
            
