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

class GatewayFactory:
    """
    Factory class to create different types of agent gateway transports and protocols.
    """
    def __init__(self):
        _gateway_endpoint = ""
        _clients = {} # do we need to store clients and receivers?
        _receivers = {}

    def create_client(self, protocol, endpoint: str, transport=None, auth=None, gateway=None):

        gateway = None
        client = None

        # if transport is specified, match it and create the corresponding gateway
        if transport:
            transport = transport.upper()
            match transport:
                case "AGP":
                    gateway = AGPGateway(endpoint, auth)
                case "NATS":
                    gateway = NatsGateway(endpoint, auth)
                case _:
                    raise ValueError(f"Unsupported transport: {transport}")
       
        # return a client for the specified protocol
        protocol = protocol.upper()
        match protocol:
            case "A2A":
                client = create_client_a2a(endpoint, gateway, auth)
            case "AP":
                client = create_client_ap(endpoint, gateway, auth)
            case "MCP":
                client = create_client_mcp(endpoint, gateway, auth)
            case "ACP":
                client = create_client_acp(endpoint, gateway, auth)
            case _:
                raise ValueError(f"Unsupported protocol: {protocol}")

        return client

    def create_receiver(self, protocol, transport: str, endpoint: str, onMessage: callable, auth=None):
        """
        Create a receiver for the specified transport and protocol.

        This will connect to a gateway and offload messages to whatever protcol is provided.

        How do we offload messages, do we have adapters for each protocol?
        """
        pass