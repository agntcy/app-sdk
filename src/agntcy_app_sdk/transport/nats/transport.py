# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os

import nats
from nats.aio.client import Client as NATS
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.message import Message
from typing import Callable, List, Optional, Any, Awaitable, AsyncIterator
from uuid import uuid4

configure_logging()
logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""


class NatsTransport(BaseTransport):
    def __init__(
        self, client: Optional[NATS] = None, endpoint: Optional[str] = None, **kwargs
    ):
        """
        Initialize the NATS transport with the given endpoint and client.
        :param endpoint: The NATS server endpoint.
        :param client: An optional NATS client instance. If not provided, a new one will be created.
        """

        if not endpoint and not client:
            raise ValueError("Either endpoint or client must be provided")
        if client and not isinstance(client, NATS):
            raise ValueError("Client must be an instance of nats.aio.client.Client")

        self._nc = client
        self.endpoint = endpoint
        self._callback = None
        self.subscriptions = []

        # connection options
        self.connect_timeout = kwargs.get("connect_timeout", 5)
        self.reconnect_time_wait = kwargs.get("reconnect_time_wait", 2)
        self.max_reconnect_attempts = kwargs.get("max_reconnect_attempts", 30)
        self.drain_timeout = kwargs.get("drain_timeout", 2)

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            logger.info("NatsTransport initialized with tracing enabled")
            self.tracing_enabled = True

    # -----------------------------------------------------------------------------
    # BaseTransport method implementations
    # -----------------------------------------------------------------------------

    # -----------------------------------------------------------------------------
    # Point-to-Point
    # -----------------------------------------------------------------------------
    async def send(self, recipient: str, message: Message, **kwargs) -> None:
        """
        Send a message to a single recipient without expecting a response.
        """
        recipient = self.santize_topic(recipient)
        logger.debug(f"Publishing {message.payload} to topic: {recipient}")

        if self._nc is None:
            raise RuntimeError(
                "NATS client is not connected, please call setup() before subscribing"
            )

        await self._nc.publish(
            recipient,
            message.serialize(),
        )

    async def request(
        self, recipient: str, message: Message, timeout: int = 60, **kwargs
    ) -> Message:
        """
        Send a message to a recipient and await a single response.
        """
        recipient = self.santize_topic(recipient)
        logger.debug(
            f"Requesting with payload: {message.payload} to topic: {recipient}"
        )

        response = await self._nc.request(
            recipient, message.serialize(), timeout=timeout, **kwargs
        )
        return Message.deserialize(response.data) if response else None

    async def request_stream(
        self, recipient: str, message: Message, timeout: int = 90, **kwargs
    ) -> AsyncIterator[Message]:
        """
        Send a request and receive a continuous stream of responses.
        """
        # reuse gather_stream implementation
        async for message in self.gather_stream(
            recipient, message, [recipient], timeout=timeout, **kwargs
        ):
            yield message

    # -----------------------------------------------------------------------------
    # Fan-Out / Publish-Subscribe
    # -----------------------------------------------------------------------------

    async def publish(self, topic: str, message: Message, **kwargs) -> None:
        """
        Publish a message to all subscribers of the topic.
        """
        # Reuse the send implementation for publish
        await self.send(topic, message, **kwargs)

    async def gather(
        self,
        topic: str,
        message: Message,
        recipients: List[str],
        message_limit: int = None,
        timeout: int = 60,
        **kwargs,
    ) -> List[Message]:
        """
        Publish a message and collect responses from multiple subscribers.
        """

        if message_limit is None:
            message_limit = len(recipients)

        responses = []
        async for resp in self.gather_stream(
            topic,
            message,
            recipients,
            message_limit=message_limit,
            timeout=timeout,
            **kwargs,
        ):
            responses.append(resp)
        return responses

    async def gather_stream(
        self,
        topic: str,
        message: Message,
        recipients: List[str],
        timeout: int = 60,
        message_limit: int = None,
        **kwargs,
    ) -> AsyncIterator[Message]:
        """
        Publish a message and yield responses from multiple subscribers as they arrive.
        """

        if self._nc is None:
            raise RuntimeError(
                "NATS client is not connected, please call setup() before subscribing"
            )

        if not recipients:
            raise ValueError(
                "recipients list must be provided for NATS COLLECT_ALL mode."
            )

        publish_topic = self.santize_topic(topic)
        reply_topic = uuid4().hex
        message.reply_to = reply_topic

        logger.info(f"Publishing to: {publish_topic} and receiving from: {reply_topic}")

        response_queue: asyncio.Queue = asyncio.Queue()
        if message_limit is None:
            message_limit = float("inf")

        async def _response_handler(nats_msg) -> None:
            msg = Message.deserialize(nats_msg.data)
            await response_queue.put(msg)

        sub = None
        try:
            # Subscribe to the reply topic to collect responses
            sub = await self._nc.subscribe(reply_topic, cb=_response_handler)

            # Publish the message
            await self.publish(topic, message)

            received = 0
            while received < message_limit:
                try:
                    msg = await asyncio.wait_for(response_queue.get(), timeout=timeout)
                    received += 1
                    logger.debug(f"Received {received} response")
                    yield msg
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout reached after {timeout}s; collected {received} response(s)"
                    )
                    break
        finally:
            if sub is not None:
                await sub.unsubscribe()

    # -----------------------------------------------------------------------------
    # Group Chat / Multi-Party Conversation
    # -----------------------------------------------------------------------------

    async def start_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str,
        **kwargs,
    ) -> List[Message]:
        """
        Create a new conversation including the given participants.
        """
        raise NotImplementedError

    async def start_streaming_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str,
        **kwargs,
    ) -> AsyncIterator[Message]:
        """
        Create a new streaming conversation including the given participants.
        """
        raise NotImplementedError

    # -----------------------------------------------------------------------------
    # Utilities and setup methods
    # -----------------------------------------------------------------------------

    @classmethod
    def from_client(cls, client: NATS) -> "NatsTransport":
        # Optionally validate client
        return cls(client=client)

    @classmethod
    def from_config(cls, endpoint: str, **kwargs) -> "NatsTransport":
        """
        Create a NATS transport instance from a configuration.
        :param gateway_endpoint: The NATS server endpoint.
        :param kwargs: Additional configuration parameters.
        """
        return cls(endpoint=endpoint, **kwargs)

    def type(self) -> str:
        return "NATS"

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic

    async def setup(self):
        if self._nc is None or not self._nc.is_connected:
            await self._connect()

    async def _connect(self):
        """Connect to the NATS server."""
        if self._nc is not None and self._nc.is_connected:
            logger.info("Already connected to NATS server")
            return

        self._nc = await nats.connect(
            self.endpoint,
            reconnect_time_wait=self.reconnect_time_wait,  # Time between reconnect attempts
            max_reconnect_attempts=self.max_reconnect_attempts,  # Retry for 2 minutes before giving up
            error_cb=self.error_cb,
            closed_cb=self.closed_cb,
            disconnected_cb=self.disconnected_cb,
            reconnected_cb=self.reconnected_cb,
            connect_timeout=self.connect_timeout,
            drain_timeout=self.drain_timeout,
        )
        logger.info("Connected to NATS server")

    async def close(self) -> None:
        """Close the NATS connection."""
        if self._nc:
            try:
                await self._nc.drain()
                await self._nc.close()
                logger.info("NATS connection closed")
            except Exception as e:
                logger.error(f"Error closing NATS connection: {e}")
        else:
            logger.warning("No NATS connection to close")

    def set_callback(self, callback: Callable[..., Awaitable[Any]]) -> None:
        """Set the message handler function."""
        self._callback = callback

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        if self._nc is None or not self._nc.is_connected:
            raise RuntimeError(
                "NATS client is not connected, please call setup() before subscribing"
            )

        if not self._callback:
            raise ValueError("Message handler must be set before starting transport")

        try:
            topic = self.santize_topic(topic)
            sub = await self._nc.subscribe(topic, cb=self._message_handler)

            self.subscriptions.append(sub)
            logger.info(f"Subscribed to topic: {topic}")
        except Exception as e:
            logger.error(f"Error subscribe to topic '{topic}': {e}")

    async def _message_handler(self, nats_msg):
        """
        Internal NATS message handler that deserializes the message and invokes the user-defined callback.
        """
        message = Message.deserialize(nats_msg.data)

        # Add reply_to from NATS message if not in payload
        if nats_msg.reply and not message.reply_to:
            message.reply_to = nats_msg.reply

        # Process the message with the registered handler
        if self._callback:
            resp = await self._callback(message)
            if not resp and message.reply_to:
                logger.warning("Handler returned no response for message.")
                err_msg = Message(
                    type="error",
                    payload="No response from handler",
                    reply_to=message.reply_to,
                )
                await self.publish(message.reply_to, err_msg)

            # publish response to the reply topic
            await self.publish(message.reply_to, resp)

    # Callbacks and error handling
    async def error_cb(self, e):
        logger.error(f"NATS error: {e}")

    async def closed_cb(self):
        logger.warning("Connection to NATS is closed.")

    async def disconnected_cb(self):
        logger.warning("Disconnected from NATS.")

    async def reconnected_cb(self):
        logger.info(f"Reconnected to NATS at {self._nc.connected_url.netloc}...")
