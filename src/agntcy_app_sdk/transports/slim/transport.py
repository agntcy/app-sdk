# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable
import os
import slim_bindings
import asyncio
import inspect
import datetime
import uuid
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport, Message
from .common import (
    create_local_app,
    split_id,
)


configure_logging()
logger = get_logger(__name__)

"""
SLIM implementation of the BaseTransport interface.
"""


class SLIMTransport(BaseTransport):
    """
    SLIM Transport implementation using the slim_bindings library.
    """

    def __init__(
        self,
        client=None,
        endpoint: Optional[str] = None,
        default_org: str = "default",
        default_namespace: str = "default",
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=10),
        message_retries: int = 2,
    ) -> None:
        self._endpoint = endpoint
        self._slim = client
        self._callback = None
        self._default_org = default_org
        self._default_namespace = default_namespace
        self.message_timeout = message_timeout
        self.message_retries = message_retries

        self._sessions = {}

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            # Initialize tracing if enabled
            from ioa_observe.sdk.instrumentations.slim import SLIMInstrumentor

            SLIMInstrumentor().instrument()
            logger.info("SLIMTransport initialized with tracing enabled")

        logger.info(f"SLIMTransport initialized with endpoint: {endpoint}")

    # ###################################################
    # BaseTransport interface methods
    # ###################################################

    @classmethod
    def from_client(cls, client, org="default", namespace="default") -> "SLIMTransport":
        # Optionally validate client
        return cls(client=client, default_org=org, default_namespace=namespace)

    @classmethod
    def from_config(
        cls, endpoint: str, org: str = "default", namespace: str = "default", **kwargs
    ) -> "SLIMTransport":
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
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)

        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        # if we are asked to provide a response, use or generate a reply_to topic
        if respond and not message.reply_to:
            message.reply_to = uuid.uuid4().hex

        resp = await self._publish(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
            message=message,
            expected_responses=1 if respond else 0,
        )

        if respond:
            return resp[0] if resp else None

    async def broadcast(
        self,
        topic: str,
        message: Message,
        expected_responses: int = 1,
        timeout: Optional[float] = 30.0,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        topic = self.santize_topic(topic)

        logger.info(
            f"Broadcasting to topic: {topic} and waiting for {expected_responses} responses"
        )

        # Generate a unique reply_to topic if not provided
        if not message.reply_to:
            message.reply_to = uuid.uuid4().hex

        # set the broadcast_id header to a unique value
        message.headers = message.headers or {}
        message.headers["broadcast_id"] = str(uuid.uuid4())

        try:
            responses = await asyncio.wait_for(
                self._publish(
                    org=self._default_org,
                    namespace=self._default_namespace,
                    topic=topic,
                    message=message,
                    expected_responses=expected_responses,
                ),
                timeout=timeout,
            )
            return responses
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {topic} timed out after {timeout} seconds"
            )
            return []

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        topic = self.santize_topic(topic)

        await self._subscribe(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
        )

        logger.info(
            f"Subscribed to {self._default_org}/{self._default_namespace}/{topic}"
        )

    # ###################################################
    # SLIM sub methods
    # ###################################################

    async def _subscribe(self, org: str, namespace: str, topic: str) -> None:
        if not self._slim:
            await self._slim_connect(org, namespace, topic)

        # await self._slim.subscribe(org, namespace, topic)

        # session_info = await self._get_session(org, namespace, topic, "pubsub")

        print("test----", dir(self._slim))

        async def background_task():
            async with self._slim:
                while True:
                    # Receive the message from the session
                    """recv_session, msg = await self._slim.receive(
                        session=session_info.id
                    )"""
                    session_info, _ = await self._slim.receive()

                    async def background_task2(session_id):
                        while True:
                            # Receive the message from the session
                            session, msg = await self._slim.receive(session=session_id)
                            msg = Message.deserialize(msg)
                            print(f"Received message: {msg}")

                            reply_to = msg.reply_to
                            msg.reply_to = None  # we will handle replies instead of the bridge receiver

                            if inspect.iscoroutinefunction(self._callback):
                                output = await self._callback(msg)
                            else:
                                output = self._callback(msg)

                            if reply_to:
                                payload = output.serialize()
                                print(f"Replying to {reply_to} with message: {output}")

                                await self._slim.publish_to(session, payload)

                                print("Replied.")

                    asyncio.create_task(background_task2(session_info.id))

        asyncio.create_task(background_task())

    async def _publish(
        self,
        org: str,
        namespace: str,
        topic: str,
        message: Message,
        expected_responses: int = 0,
    ) -> None:
        if not self._slim:
            await self._slim_connect(org, namespace, uuid.uuid4().hex)

        logger.debug(f"Publishing to topic: {topic}")

        async with self._slim:
            # Set a slim route to this topic, enabling outbound messages to this topic
            remote_name = split_id(f"{org}/{namespace}/{topic}")
            await self._slim.set_route(remote_name)

            session = await self._slim.create_session(
                slim_bindings.PySessionConfiguration.FireAndForget(
                    max_retries=5,
                    timeout=datetime.timedelta(seconds=5),
                    sticky=True,
                    mls_enabled=False,
                )
            )

            _, reply = await self._slim.request_reply(
                session,
                message.serialize(),
                remote_name,
                timeout=datetime.timedelta(seconds=5),
            )

            if reply:
                reply = Message.deserialize(reply)
                if expected_responses > 0:
                    return [reply]
                else:
                    return None

    async def _slim_connect(
        self, org: str, namespace: str, topic: str, retries=3
    ) -> None:
        # create new gateway object
        logger.info(
            f"Creating new gateway for org: {org}, namespace: {namespace}, topic: {topic}"
        )

        self._slim: slim_bindings.Slim = await create_local_app(
            f"{org}/{namespace}/{topic}",
            slim={
                "endpoint": self._endpoint,
                "tls": {"insecure": True},
            },
            enable_opentelemetry=False,
            shared_secret="<shared_secret>",
            jwt=None,
            bundle=None,
            audience=None,
        )

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
