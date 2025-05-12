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
from a2a.client import A2AClient
from a2a.server import A2AServer
from ..base_protocol import BaseProtocol

configure_logging()
logger = get_logger(__name__)

class A2AFactory(BaseProtocol):
    def get_type(self):
        """
        Return the transport type as a string.
        """
        return "A2A"
    
    async def create_client(self, url, transport=None, auth=None):
        """
        Create an A2A client, passing in the transport and authentication details.
        """

        # Create an A2A client
        async with httpx.AsyncClient() as httpx_client:
            client = await A2AClient.get_client_from_agent_card_url(
                httpx_client, url
            )
            print('Connection successful.')

            return client

    def create_receiver(self):
        """
        A receiver should connect to a gateway and then offload messages to A2A agents

        server = A2AServer(
            agent_card=get_agent_card(host, port), request_handler=request_handler
        )
        """
        pass