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

import asyncio
import nats
from ..logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

"""
Implementations of the BaseGateway class for different protocols.
These classes should implement the abstract methods defined in BaseGateway.
"""

class NatsGateway():
    def __init__(self, endpoint: str, auth=None):
        self.client = None
        self.endpoint = endpoint
        self.auth = auth
        
    async def _connect(self):
        self.client = await nats.connect(self.endpoint)
        logger.info("Connected to NATS server")

    async def request(self, org: str, ns: str, agent: str, message: bytes) -> bytes:
        if self.client is None:
            await self._connect()
        
        subject = f"{org}.{ns}.{agent}"
        logger.info(f"Sending request to {subject}")
        response = await self.client.request(subject, message)
        logger.info(f"Received response from {subject}")
        return response.data
    
    async def subscribe(self, org: str, ns: str, agent: str, callback):
        if self.client is None:
            await self._connect()

        subject = f"{org}.{ns}.{agent}"
        logger.info(f"Subscribing to {subject}")
        await self.client.subscribe(subject, cb=callback)

    async def publish(self, org: str, ns: str, agent: str, message: bytes):
        if self.client is None:
            await self._connect()
            
        subject = f"{org}.{ns}.{agent}"
        logger.info(f"Publishing message to {subject}")
        await self.client.publish(subject, message)
        logger.info(f"Message published to {subject}")
