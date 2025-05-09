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

class Protocol(Enum):
    """
    Enum for supported agent protocols.
    """
    A2A = "A2A"
    AP = "AP"
    MCP = "MCP"
    ACP = "ACP"

class Transport(Enum):
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
    def __init__(self):
        _clients = {} # do we need to store clients and receivers?
        _receivers = {}

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
            transport = Transport(transport.upper())
        except ValueError:
            raise ValueError(f"Unsupported transport: {transport}")

        match transport:
            case Transport.NONE:
                pass # noop
            case Transport.AGP:
                gateway = AGPGateway(gateway_endpoint, auth)
            case Transport.NATS:
                gateway = NatsGateway(gateway_endpoint, auth)
       
        try:
            protocol = Protocol(protocol.upper())
        except ValueError:
            raise ValueError(f"Unsupported protocol: {protocol}")
        
        match protocol:
            case Protocol.A2A:
                client = create_client_a2a(agent_endpoint, gateway, auth)
            case Protocol.AP:
                client = create_client_ap(agent_endpoint, gateway, auth)
            case Protocol.MCP:
                client = create_client_mcp(agent_endpoint, gateway, auth)
            case Protocol.ACP:
                client = create_client_acp(agent_endpoint, gateway, auth)

        return client

    def create_receiver(
        self,
        protocol: str,
        onMessage: callable,
        agent_endpoint: str,
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
        pass
