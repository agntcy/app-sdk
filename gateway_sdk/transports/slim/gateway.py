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
import slim_bindings
import asyncio
import inspect
import datetime
from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.transports.transport import BaseTransport, Message

configure_logging()
logger = get_logger(__name__)

"""
Implementations of the BaseGateway class for different protocols.
These classes should implement the abstract methods defined in BaseGateway.
"""


class SLIMGateway(BaseTransport):
    """
    SLIM Gateway implementation using the slim_bindings library.
    """

    def __init__(
        self,
        client=None,
        endpoint: Optional[str] = None,
        default_org: str = "default",
        default_namespace: str = "default",
    ) -> None:
        self._endpoint = endpoint
        self._gateway = client
        self._callback = None
        self._default_org = default_org
        self._default_namespace = default_namespace

        self._sessions = {}

        logger.info(f"SLIMGateway initialized with endpoint: {endpoint}")

    # ###################################################
    # BaseTransport interface methods
    # ###################################################

    @classmethod
    def from_client(cls, client, org="default", namespace="default") -> "SLIMGateway":
        # Optionally validate client
        return cls(client=client, default_org=org, default_namespace=namespace)

    @classmethod
    def from_config(
        cls, endpoint: str, org: str = "default", namespace: str = "default", **kwargs
    ) -> "SLIMGateway":
        """
        Create a SLIM transport instance from a configuration.
        :param endpoint: The SLIM server endpoint.
        :param org: The organization name.
        :param namespace: The namespace name.
        :param kwargs: Additional configuration parameters.
        """
        return cls(
            endpoint=endpoint, default_org=org, default_namespace=namespace, **kwargs
        )

    def type(self) -> str:
        """Return the transport type."""
        return "SLIM"

    async def close(self) -> None:
        pass

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = handler

    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)

        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        if respond:
            message.reply_to = topic  # TODO: set this appropriately

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
    # SLIM specific methods
    # ###################################################

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic

    async def _create_gateway(self, org: str, namespace: str, topic: str) -> None:
        # create new gateway object
        logger.info(
            f"Creating new gateway for org: {org}, namespace: {namespace}, topic: {topic}"
        )

        self._gateway = await slim_bindings.Slim.new(org, namespace, topic)

        # Connect to slim server
        _ = await self._gateway.connect(
            {
                "endpoint": self._endpoint,
                "tls": {"insecure": True},
            }  # TODO: handle with config input
        )

        logger.info(f"connected to gateway @{self._endpoint}")

    async def _subscribe(self, org: str, namespace: str, topic: str) -> None:
        if not self._gateway:
            await self._create_gateway(org, namespace, topic)

        await self._gateway.subscribe(org, namespace, topic)

        session_info = await self._get_session(org, namespace, topic, "pubsub")

        async with self._gateway:
            while True:
                # Receive the message from the session
                recv_session, msg = await self._gateway.receive(session=session_info.id)

                msg = Message.deserialize(msg)

                logger.info(f"Received message: {msg}")

                reply_to = msg.reply_to
                msg.reply_to = None  # we will handle reply with the session

                if inspect.iscoroutinefunction(self._callback):
                    output = await self._callback(msg)
                else:
                    output = self._callback(msg)

                if reply_to:
                    await self._gateway.publish_to(recv_session, output.serialize())

    async def _publish(
        self, org: str, namespace: str, topic: str, message: Message
    ) -> None:
        if not self._gateway:
            # TODO: create a hash for the topic so its private since subscribe hasn't been called
            await self._create_gateway("default", "default", "default")

        payload = message.serialize()
        logger.debug(f"Publishing {payload} to topic: {topic}")

        await self._gateway.set_route(org, namespace, topic)

        session_info = await self._get_session(org, namespace, topic, "pubsub")

        async with self._gateway:
            # Send the message
            await self._gateway.publish(
                session_info,
                message.serialize(),
                org,
                namespace,
                topic,
            )

            if message.reply_to:
                session_info, msg = await self._gateway.receive(session=session_info.id)
                response = Message.deserialize(msg)
                return response

    async def _get_session(self, org, namespace, topic, session_type):
        session_key = f"{org}_{namespace}_{topic}_{session_type}"

        # TODO: handle different session types
        if session_key in self._sessions:
            session_info = self._sessions[session_key]
        else:
            session_info = await self._gateway.create_session(
                slim_bindings.PySessionConfiguration.Streaming(
                    slim_bindings.PySessionDirection.BIDIRECTIONAL,
                    topic=slim_bindings.PyAgentType(org, namespace, topic),
                    max_retries=5,  # TODO: set this from config
                    timeout=datetime.timedelta(seconds=5),
                )
            )
            self._sessions[session_key] = session_info

        return session_info
