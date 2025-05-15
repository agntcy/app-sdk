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
from gateway_sdk.transports.base_transport import BaseTransport
from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.protocols.message import Message
from typing import Callable, Dict, Optional

configure_logging()
logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""

class NatsGateway(BaseTransport):
    def __init__(self, endpoint: str, *args, **kwargs):
        self.type = "NATS"
        self._nc = None
        self.endpoint = endpoint
        self._callback = None
        self.subscriptions = []

    def get_type(self) -> str:
        return self.type

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
        
    async def _connect(self):
        self._nc = await nats.connect(self.endpoint)
        logger.info("Connected to NATS server")

    async def close(self) -> None:
        """Close the NATS connection."""
        if self._nc:
            await self._nc.close()
            logger.info("NATS connection closed")
        else:
            logger.warning("No NATS connection to close")

    def set_callback(
        self, 
        callback: Callable[[Message], asyncio.Future]
    ) -> None:
        """Set the message handler function."""
        self._callback = callback

    async def subscribe(self, topic: str) -> None:
        topic = self.santize_topic(topic)

        """Subscribe to a topic with a callback."""
        if self._nc is None:
            await self._connect()
        
        if not self._callback:
            raise ValueError("Message handler must be set before starting transport")
        
        sub = await self._nc.subscribe(topic, cb=self._message_handler)
        self.subscriptions.append(sub)
        logger.info(f"Subscribed to topic: {topic}")

    async def publish(
        self, 
        topic: str, 
        message: Message, 
        respond: Optional[bool] = False,
        headers: Optional[Dict[str, str]] = None,
        timeout = 5
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)
        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        if self._nc is None:
            await self._connect()

        if respond:
            resp = await self._nc.request(
                self.santize_topic(topic),
                message.serialize(),
                headers=headers,
                timeout=timeout
            )

            message = Message.deserialize(resp.data)
            return message
        else:
            await self._nc.publish(
                topic,
                message.serialize(),
            )

    async def _message_handler(self, nats_msg):
        """Internal handler for NATS messages."""
        message = Message.deserialize(nats_msg.data)
        
        # Add reply_to from NATS message if not in payload
        if nats_msg.reply and not message.reply_to:
            message.reply_to = nats_msg.reply
            
        # Process the message with the registered handler
        if self._callback:
            await self._callback(message)
