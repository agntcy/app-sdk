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
from nats.aio.client import Client as NATS
from gateway_sdk.transports.transport import BaseTransport
from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.protocols.message import Message
from typing import Callable, Dict, Optional
from opentelemetry.propagate import inject, extract
from opentelemetry import trace

configure_logging()
logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""

class NatsGateway(BaseTransport):
    def __init__(self, endpoint: str, client: Optional[NATS] = None, **kwargs):
        """
        Initialize the NATS transport with the given endpoint and client.
        :param endpoint: The NATS server endpoint.
        :param client: An optional NATS client instance. If not provided, a new one will be created.
        """

        self._nc = client
        self.endpoint = endpoint
        self._callback = None
        self.subscriptions = []

    def type(self) -> str:
        return "NATS"

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
        
    async def _connect(self):
        """Connect to the NATS server."""
        if self._nc:
            logger.info("Already connected to NATS server")
            return
        
        self._nc = await nats.connect(
            self.endpoint,
            reconnect_time_wait=2,             # Time between reconnect attempts
            max_reconnect_attempts=30,         # Retry for 2 minutes before giving up
            error_cb=self.error_cb,
            closed_cb=self.closed_cb,
            disconnected_cb=self.disconnected_cb,
            reconnected_cb=self.reconnected_cb,
            connect_timeout=5,
            drain_timeout=2,
        )
        logger.info("Connected to NATS server")

    async def close(self) -> None:
        """Close the NATS connection."""
        if self._nc:
            await self._nc.drain()
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

        if headers:
            # if headers are provided, add them to the message
            for key, value in headers.items():
                message.headers[key] = value
        else:
            headers = message.headers

        ctx = extract(message.headers)
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("nats_publish", context=ctx):

            inject(carrier=headers)

            # add tracing headers to the message
            message.headers = headers

            try:
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
            except nats.errors.TimeoutError:
                logger.error(f"Timeout while publishing to {topic}")
                raise
            except nats.errors.NatsError as e:
                logger.error(f"NATS error while publishing to {topic}: {e}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error while publishing to {topic}: {e}")
                raise

    async def _message_handler(self, nats_msg):
        """Internal handler for NATS messages."""
        message = Message.deserialize(nats_msg.data)
        
        # Add reply_to from NATS message if not in payload
        if nats_msg.reply and not message.reply_to:
            message.reply_to = nats_msg.reply
            
        # Process the message with the registered handler
        if self._callback:
            await self._callback(message)

    # Callbacks and error handling
    async def error_cb(self, e):
        logger.error(f"NATS error: {e}")

    async def closed_cb(self):
        logger.warning("Connection to NATS is closed.")

    async def disconnected_cb(self):
        logger.warning("Disconnected from NATS.")

    async def reconnected_cb(self):
        logger.info(f"Reconnected to NATS at {self._nc.connected_url.netloc}...")
