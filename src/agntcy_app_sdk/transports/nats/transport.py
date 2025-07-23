# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import nats
from nats.aio.client import Client as NATS
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from typing import Callable, List, Optional
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

    async def _connect(self):
        """Connect to the NATS server."""
        if self._nc is not None and self._nc.is_connected:
            logger.info("Already connected to NATS server")
            return

        self._nc = await nats.connect(
            self.endpoint,
            reconnect_time_wait=2,  # Time between reconnect attempts
            max_reconnect_attempts=30,  # Retry for 2 minutes before giving up
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

    def set_callback(self, callback: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = callback

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        if self._nc is None:
            await self._connect()

        if not self._callback:
            raise ValueError("Message handler must be set before starting transport")

        topic = self.santize_topic(topic)
        sub = await self._nc.subscribe(topic, cb=self._message_handler)
        self.subscriptions.append(sub)
        logger.info(f"Subscribed to topic: {topic}")

    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
        timeout=10,
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)
        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        if self._nc is None:
            await self._connect()

        if message.headers is None:
            message.headers = {}

        try:
            if respond:
                resp = await self._nc.request(
                    topic,
                    message.serialize(),
                    headers=message.headers,
                    timeout=timeout,
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
        except Exception as e:
            logger.error(f"Unexpected error while publishing to {topic}: {e}")
            raise

    async def broadcast(
        self,
        topic: str,
        message: Message,
        expected_responses: int = 1,
        timeout: Optional[float] = 30.0,
    ) -> List[Message]:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        if self._nc is None:
            await self._connect()

        publish_topic = self.santize_topic(topic)
        reply_topic = uuid4().hex
        message.reply_to = reply_topic
        logger.info(
            f"Broadcasting to: {publish_topic} and receiving from: {reply_topic}"
        )

        response_queue: asyncio.Queue = asyncio.Queue()

        async def _response_handler(nats_msg) -> None:
            msg = Message.deserialize(nats_msg.data)
            await response_queue.put(msg)

        # Subscribe to the reply topic to handle responses
        sub = await self._nc.subscribe(reply_topic, cb=_response_handler)
        await self.publish(
            topic,
            message,
            respond=False,  # tell receivers to reply to the reply_topic
        )

        logger.info(
            f"Collecting up to {expected_responses} response(s) with timeout={timeout}s..."
        )
        responses: List[Message] = []

        try:
            # Use a timeout to collect responses
            async def collect_responses():
                while len(responses) < expected_responses:
                    msg = await asyncio.wait_for(response_queue.get(), timeout=timeout)
                    responses.append(msg)
                    logger.info(f"Received {len(responses)} response(s)")

            await collect_responses()

        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout reached after {timeout}s; collected {len(responses)} response(s)"
            )

        finally:
            # Clean up request specific subscription
            await sub.unsubscribe()

        return responses

    async def _message_handler(self, nats_msg):
        """Internal handler for NATS messages."""
        message = Message.deserialize(nats_msg.data)

        # Add reply_to from NATS message if not in payload, receiver bridge may use it
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
