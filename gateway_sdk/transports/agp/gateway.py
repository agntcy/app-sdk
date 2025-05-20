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

from typing import Optional, Dict, Callable
import agp_bindings
from agp_bindings import GatewayConfig
import asyncio
import inspect
import json
from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.transports.transport import BaseTransport, Message

configure_logging()
logger = get_logger(__name__)

"""
Implementations of the BaseGateway class for different protocols.
These classes should implement the abstract methods defined in BaseGateway.
"""

class AGPGateway(BaseTransport):
    """
    AGP Gateway implementation using the agp_bindings library.
    """
    def __init__(
        self, 
        endpoint: str, 
        gateway=None, 
        default_org: str = "default", 
        default_namespace: str = "default"
    ) -> None:
        
        self.endpoint = endpoint
        self.gateway = None
        self._callback = None
        self._default_org = default_org
        self._default_namespace = default_namespace

        logger.info(f"AGPGateway initialized with endpoint: {endpoint}")

    # ###################################################
    # BaseTransport interface methods
    # ###################################################

    def type(self) -> str:
        """Return the transport type."""
        return "AGP"

    async def close(self) -> None:
       pass

    def set_callback(
        self, 
        handler: Callable[[Message], asyncio.Future]
    ) -> None:
        """Set the message handler function."""
        self._callback = handler

    async def publish(
        self, 
        topic: str, 
        message: Message, 
        respond: Optional[bool] = False,
        headers: Optional[Dict[str, str]] = None
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)

        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        if respond:
            message.reply_to = topic # TODO: set this appropriately

        resp = await self._publish(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
            message=message,
        )

        if respond:
            return resp

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        topic = self.santize_topic(topic)

        await self._subscribe(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
        )

        logger.info(f"Subscribed to topic: {topic}")

    # ###################################################
    # AGP specific methods
    # ###################################################

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic

    async def _create_gateway(self, org: str, namespace: str, topic: str) -> None:
        # create new gateway object
        logger.info(f"Creating new gateway for org: {org}, namespace: {namespace}, topic: {topic}")
        self.gateway = await agp_bindings.Gateway.new(org, namespace, topic)

        # Configure gateway
        config = GatewayConfig(endpoint=self.endpoint, insecure=True)
        self.gateway.configure(config)

        # Connect to remote gateway server
        _ = await self.gateway.connect()

        logger.info(f"connected to gateway @{self.endpoint}")

    async def _subscribe(self, org: str, namespace: str, topic: str) -> None:
        if not self.gateway:
            await self._create_gateway(org, namespace, topic)

        async with self.gateway:
            # Wait for a message and reply in a loop
            while True:
                session_info, _ = await self.gateway.receive()

                async def background_task(session_id):
                    while True:
                        # Receive the message from the session
                        session, msg = await self.gateway.receive(session=session_id)
                        
                        msg = Message.deserialize(msg)

                        logger.debug(f"Received message: {msg}")

                        reply_to = msg.reply_to
                        msg.reply_to = None # we will handle reply with the session

                        if inspect.iscoroutinefunction(self._callback):
                            output = await self._callback(msg)
                        else:
                            output = self._callback(msg)

                        if reply_to:
                            await self.gateway.publish_to(session, output.serialize())

                asyncio.create_task(background_task(session_info.id))

    async def _publish(self, org: str, namespace: str, topic: str, message: Message) -> None:
        if not self.gateway:
            # TODO: create a hash for the topic so its private since subscribe hasnt been called
            await self._create_gateway("default", "default", "default")

        payload = message.serialize()
        logger.debug(f"Publishing {payload} to topic: {topic}")

        async with self.gateway:
            # Create a route to the remote ID
            await self.gateway.set_route(org, namespace, topic)

            # create a session
            session = await self.gateway.create_ff_session(agp_bindings.PyFireAndForgetConfiguration())

            # Send the message
            await self.gateway.publish(
                session,
                message.serialize(),
                org,    
                namespace,
                topic,
            )

            if message.reply_to:
                session_info, msg = await self.gateway.receive(session=session.id)
                response = Message.deserialize(msg)
                return response