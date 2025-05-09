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

from .a2a.gateway import create_client as create_client_a2a
from .a2a.gateway import create_receiver as create_receiver_a2a

from .ap.gateway import create_client as create_client_ap
from .ap.gateway import create_receiver as create_receiver_ap

from .mcp.gateway import create_client as create_client_mcp
from .mcp.gateway import create_receiver as create_receiver_mcp

from .acp.gateway import create_client as create_client_acp
from .acp.gateway import create_receiver as create_receiver_acp

from enum import Enum

class ProtocolType(Enum):
    """
    Enum for supported agent protocols.
    """
    A2A = "A2A"
    AP = "AP"
    MCP = "MCP"
    ACP = "ACP"

class TransportType(Enum):
    """
    Enum for supported transports.
    """
    NONE = ""
    AGP = "AGP"
    NATS = "NATS"
    # KAFKA = "KAFKA"  # Uncomment if Kafka transport is implemented
    # MQTT = "MQTT"    # Uncomment if MQTT transport is implemented

class GatewayFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """
    def __init__(self, enable_logging: bool = True, enable_tracing: bool = False):
        self.enable_logging = enable_logging
        self.enable_tracing = enable_tracing

        # manage the state of the factory
        # TODO: define a state interface
        self._clients = {} 
        self._receivers = {}
        self._sessions = {}

    def create_client(
        self, protocol: str, 
        agent_endpoint: str, 
        gateway_endpoint: str = "", 
        transport: str = "", 
        auth=None
    ):

        gateway = None
        client = None

        # if transport is specified, match it and create the corresponding gateway
        try:
            transport = TransportType(transport.upper()) # TODO: just pass in a transport object?
        except ValueError:
            raise ValueError(f"Unsupported transport: {transport}")

        match transport:
            case TransportType.NONE:
                pass # noop
            case TransportType.AGP:
                gateway = AGPGateway(gateway_endpoint, auth)
            case TransportType.NATS:
                gateway = NatsGateway(gateway_endpoint, auth)
       
        try:
            protocol = ProtocolType(protocol.upper())
        except ValueError:
            raise ValueError(f"Unsupported protocol: {protocol}")
        
        match protocol:
            case ProtocolType.A2A:
                client = create_client_a2a(agent_endpoint, gateway, auth)
            case ProtocolType.AP:
                client = create_client_ap(agent_endpoint, gateway, auth)
            case ProtocolType.MCP:
                client = create_client_mcp(agent_endpoint, gateway, auth)
            case ProtocolType.ACP:
                client = create_client_acp(agent_endpoint, gateway, auth)

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

        Connects to a gateway and offloads messages using the provided protocol.

        Args:
            protocol (str): The protocol to use for offloading messages.
            onMessage (callable): Callback function to handle incoming messages.
            agent_endpoint (str): Endpoint for the agent.
            gateway_endpoint (str, optional): Endpoint for the gateway. Defaults to "".
            transport (str, optional): Transport type (e.g., "websocket", "http"). Defaults to "".
            auth (any, optional): Optional authentication info or credentials.

        Note:
            - How do we offload messages? Do we have adapters for each protocol?
        """
        
        gateway = None
        receiver = None

        # if transport is specified, match it and create the corresponding gateway
        try:
            transport = TransportType(transport.upper()) # TODO: just pass in a transport object?
        except ValueError:
            raise ValueError(f"Unsupported transport: {transport}")
        match transport:
            case TransportType.AGP:
                gateway = AGPGateway(gateway_endpoint, auth)
            case TransportType.NATS:
                gateway = NatsGateway(gateway_endpoint, auth)
            case TransportType.NONE:
                raise ValueError("Transport type is required for receiver creation")
            
        gateway.subscribe()
            
        
            
