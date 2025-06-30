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

from typing import Any

import asyncio
from mcp.client.streamable_http import streamablehttp_client

from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.protocols.message import Message
from gateway_sdk.protocols.protocol import BaseAgentProtocol
from gateway_sdk.transports.transport import BaseTransport 
from gateway_sdk.transports.streamable_http.gateway import StreamableHTTPGateway 

configure_logging()
logger = get_logger(__name__)

class MCPProtocol:
    """
    MCPProtocol is a placeholder for the MCP protocol implementation.
    It should define methods to create clients, receivers, and handle messages.
    """
    def type(self):
        return "MCP"

    async def create_client(
        self,
        url: str = None,
        transport: BaseTransport = None,
        **kwargs,
    ) -> StreamableHTTPGateway:
        """
        Create a client for the MCP protocol.
        """
        logger.info(f"Creating MCP client with URL: {url}")
        if not url:
            raise ValueError("MCP Server URL must be provided to create an MCP client")
        
        # overrides the transport to use StreamableHTTPGateway
        transport = StreamableHTTPGateway(endpoint=url)

        # Create a streamable HTTP client for MCP
        try:
            client = streamablehttp_client(url=url)
            await transport.connect(client)
            return transport
        except Exception as e:
            await transport.close()
            logger.error(f"Failed to create MCP client: {e}")
            raise
    
    def message_translator(self, request: Any) -> Message:
        """
        Translate a request into a Message object.
        This method should be implemented to convert the request format
        into the Message format used by the MCP protocol.
        """
        raise NotImplementedError(
            "Message translation is not implemented for MCP protocol"
        )
    
    def create_ingress_handler(self, *args, **kwargs) -> Any:
        """
        Create an ingress handler for the MCP protocol.
        This method should define how to handle incoming messages.
        """
        raise NotImplementedError(
            "Ingress handler creation is not implemented for MCP protocol"
        )




