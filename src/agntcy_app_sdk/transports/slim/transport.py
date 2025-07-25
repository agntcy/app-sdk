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
    ) -> None:
        self._endpoint = endpoint
        self._gateway = client
        self._callback = None
        self._default_org = default_org
        self._default_namespace = default_namespace

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
        if not self._gateway:
            await self._create_gateway(org, namespace, topic)

        await self._gateway.subscribe(org, namespace, topic)

        session_info = await self._get_session(org, namespace, topic, "pubsub")

        async def background_task():
            async with self._gateway:
                while True:
                    # Receive the message from the session
                    recv_session, msg = await self._gateway.receive(
                        session=session_info.id
                    )

                    msg = Message.deserialize(msg)

                    logger.debug(f"Received message: {msg}")

                    reply_to = msg.reply_to
                    msg.reply_to = (
                        None  # we will handle replies instead of the bridge receiver
                    )

                    if inspect.iscoroutinefunction(self._callback):
                        output = await self._callback(msg)
                    else:
                        output = self._callback(msg)

                    if reply_to:
                        # set a unique broadcast_id if not already set
                        output.headers = output.headers or {}
                        output.headers["broadcast_id"] = msg.headers.get(
                            "broadcast_id", str(uuid.uuid4())
                        )

                        payload = output.serialize()

                        # Set a slim route to the reply_to topic to enable outbound messages
                        await self._gateway.set_route(org, namespace, reply_to)

                        await self._gateway.publish(
                            recv_session,
                            payload,
                            org,
                            namespace,
                            reply_to,
                        )

                        logger.debug(f"Replied to {reply_to} with message: {output}")

        asyncio.create_task(background_task())

    async def _publish(
        self,
        org: str,
        namespace: str,
        topic: str,
        message: Message,
        expected_responses: int = 0,
    ) -> None:
        if not self._gateway:
            await self._create_gateway(org, namespace, uuid.uuid4().hex)

        logger.debug(f"Publishing to topic: {topic}")

        # Set a slim route to this topic, enabling outbound messages to this topic
        await self._gateway.set_route(org, namespace, topic)
        if message.reply_to:
            logger.debug(f"Setting reply_to topic: {message.reply_to}")
            # to get responses, we need to subscribe to the reply_to topic
            await self._gateway.subscribe(org, namespace, message.reply_to)

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

            responses = []
            while len(responses) < expected_responses and expected_responses > 0:
                # Wait for a response if requested
                session_info, msg = await self._gateway.receive(session=session_info.id)
                response = Message.deserialize(msg)

                # Check if the response is from the same broadcast
                broadcast_id = message.headers.get("broadcast_id")
                if (
                    broadcast_id
                    and response.headers.get("broadcast_id") != broadcast_id
                ):
                    logger.warning(
                        f"Received response with different broadcast_id: {response.headers.get('broadcast_id')}"
                    )
                    continue
                responses.append(response)

            return responses

    async def _get_session(self, org, namespace, topic, session_type):
        session_key = f"{org}_{namespace}_{topic}_{session_type}"

        # TODO: handle different session types
        if session_key in self._sessions:
            session_info = self._sessions[session_key]
            logger.debug(f"Reusing existing session: {session_key}")
        else:
            session_info = await self._gateway.create_session(
                slim_bindings.PySessionConfiguration.Streaming(
                    slim_bindings.PySessionDirection.BIDIRECTIONAL,
                    topic=slim_bindings.PyAgentType(org, namespace, topic),
                    max_retries=5,  # TODO: set this from config
                    timeout=datetime.timedelta(seconds=5),
                )
            )
            logger.debug(f"Created new session: {session_key}")
            self._sessions[session_key] = session_info

        return session_info

    async def _create_gateway(
        self, org: str, namespace: str, topic: str, retries=3
    ) -> None:
        # create new gateway object
        logger.info(
            f"Creating new gateway for org: {org}, namespace: {namespace}, topic: {topic}"
        )

        self._gateway = await slim_bindings.Slim.new(org, namespace, topic)

        for _ in range(retries):
            try:
                # Attempt to connect to the SLIM server
                # Connect to slim server
                _ = await self._gateway.connect(
                    {
                        "endpoint": self._endpoint,
                        "tls": {"insecure": True},
                    }  # TODO: handle with config input
                )

                logger.info(f"connected to gateway @{self._endpoint}")
                return  # Successfully connected, exit the loop
            except Exception as e:
                logger.error(f"Failed to connect to SLIM server: {e}")
                await asyncio.sleep(1)

        raise RuntimeError(f"Failed to connect to SLIM server after {retries} retries.")

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
