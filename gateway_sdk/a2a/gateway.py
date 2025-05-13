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

from ..logging_config import configure_logging, get_logger
import httpx
from a2a.client import A2AClient, A2ACardResolver
from a2a.server import A2AServer
from ..base_protocol import BaseAgentProtocol
from ..base_transport import BaseTransport
from ..adapters.base import RequestHandler
from ..adapters.asgi_adapter import AsgiHandler

configure_logging()
logger = get_logger(__name__)

class A2AProtocol(BaseAgentProtocol):
    def get_type(self):
        return "A2A"
    
    async def create_client(self, url, transport: BaseTransport = None, auth=None):
        """
        Create an A2A client, passing in the transport and authentication details.
        """

        httpx_client = httpx.AsyncClient()
        client = await A2AClient.get_client_from_agent_card_url(
            httpx_client, url
        )

        # fix bug in A2AClient.get_client_from_agent_card_url where the card is not being set
        if not hasattr(client, "agent_card"):
            agent_card = await A2ACardResolver(
                httpx_client, base_url=url,
            ).get_agent_card()
            client.agent_card = agent_card

        if transport:
            topic = f"{agent_card.name}_{agent_card.version}" # TODO: use a method to generate the topic
            transport.bind_to_topic(topic)
            transport.set_message_translator(AsgiHandler.translate_outgoing_request)
            client.httpx_client = transport

        return client

    def create_ingress_handler(self, server: A2AServer) -> RequestHandler:
        """
        Create a bridge between the A2A server and the ASGI adapter.
        """
        # Create an ASGI adapter
        asgi_adapter = AsgiHandler(server.app())
        return asgi_adapter
    
