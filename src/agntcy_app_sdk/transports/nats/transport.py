# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import os

import nats
from nats.aio.client import Client as NATS
from opentelemetry import trace
from agntcy_app_sdk.transports.transport import BaseTransport, ResponseMode
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from typing import Callable, List, Optional, Tuple, Any
from uuid import uuid4

configure_logging()
logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""

tracer = trace.get_tracer(__name__)


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
            await self._nc.drain()
            await self._nc.close()
            logger.info("NATS connection closed")
        else:
            logger.warning("No NATS connection to close")

    async def publish(
        self,
        topic: str,
        message: Message,
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)
        logger.debug(f"Publishing {message.payload} to topic: {topic}")

        if self._nc is None:
            raise RuntimeError(
                "NATS client is not connected, please call setup() before subscribing"
            )

        if message.headers is None:
            message.headers = {}

        with tracer.start_as_current_span("nats.publish") as span:
            span.set_attribute("topic", topic)
            id_, message_id = self._extract_message_payload_ids(message.payload)

            if id_:
                span.set_attribute("message.id", id_)
            if message_id:
                span.set_attribute("message.payload.messageId", message_id)

            await self._nc.publish(
                topic,
                message.serialize(),
            )

    async def request(
        self,
        topic: str,
        message: Message,
        response_mode: ResponseMode = ResponseMode.FIRST,
        timeout: Optional[float] = 60.0,
        **kwargs,
    ) -> Optional[Message]:
        topic = self.santize_topic(topic)

        match response_mode:
            case ResponseMode.FIRST:
                return await self._request_first(
                    topic=topic, message=message, timeout=timeout, **kwargs
                )
            case ResponseMode.COLLECT_N:
                raise NotImplementedError(
                    "COLLECT_N response mode is not yet implemented."
                )
            case ResponseMode.COLLECT_ALL:
                return await self._request_all(
                    topic=topic, message=message, timeout=timeout, **kwargs
                )
            case ResponseMode.GROUP:
                raise NotImplementedError("GROUP response mode is not yet implemented.")
            case _:
                raise ValueError(f"Unknown response mode: {response_mode}")

    async def _request_first(
        self, topic: str, message: Message, timeout: float, **kwargs
    ) -> Optional[Message]:
        with tracer.start_as_current_span("nats.request") as span:
            span.set_attribute("topic", topic)
            id_, message_id = self._extract_message_payload_ids(message.payload)

            if id_:
                span.set_attribute("message.id", id_)
            if message_id:
                span.set_attribute("message.payload.messageId", message_id)

            response = await self._nc.request(
                topic, message.serialize(), timeout=timeout, **kwargs
            )
            return Message.deserialize(response.data) if response else None

    async def _request_all(
        self,
        topic: str,
        message: Message,
        recipients: List[str] = None,
        timeout: float = 30.0,
    ) -> List[Message]:
        """
        Send a message to topic and wait for a response from all recipients
        or until the timeout is reached.
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
        expected_responses = len(recipients)

        async def _response_handler(nats_msg) -> None:
            msg = Message.deserialize(nats_msg.data)
            await response_queue.put(msg)

        responses: List[Message] = []

        async def collect_responses():
            while len(responses) < expected_responses:
                msg = await asyncio.wait_for(response_queue.get(), timeout=timeout)
                responses.append(msg)
                logger.info(f"Received {len(responses)} response(s)")

        sub = None
        parent_cm = None
        # Start the parent span so all NATS operations (including unsubscribe) are children
        parent_cm = tracer.start_as_current_span("nats.broadcast")
        parent_span = parent_cm.__enter__()
        parent_span.set_attribute("topic", topic)

        try:
            with tracer.start_as_current_span("nats.subscribe") as sub_span:
                sub_span.set_attribute("topic", reply_topic)
                sub = await self._nc.subscribe(reply_topic, cb=_response_handler)

                # Publish the message
                # Note: the publish() already creates a child span
                await self.publish(
                    topic,
                    message,
                )

                logger.info(
                    f"Collecting up to {expected_responses} response(s) with timeout={timeout}s..."
                )

                # Collect responses
                with tracer.start_as_current_span("nats.collect_responses") as col_span:
                    col_span.set_attribute("expected_responses", expected_responses)
                    await collect_responses()

        except asyncio.TimeoutError:
            logger.warning(
                f"Timeout reached after {timeout}s; collected {len(responses)} response(s)"
            )
        finally:
            if sub is not None:
                with tracer.start_as_current_span("nats.unsubscribe") as unsub_span:
                    unsub_span.set_attribute("topic", reply_topic)
                    await sub.unsubscribe()

            # Exit parent span context after all child spans are finished
            if parent_cm is not None:
                parent_cm.__exit__(None, None, None)  # Clean up span context

        return responses

    def set_callback(self, callback: Callable[[Message], asyncio.Future]) -> None:
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

            with tracer.start_as_current_span("nats.subscribe") as span:
                span.set_attribute("topic", topic)
                sub = await self._nc.subscribe(topic, cb=self._message_handler)

            self.subscriptions.append(sub)
            logger.info(f"Subscribed to topic: {topic}")
        except Exception as e:
            logger.error(f"Error subscribe to topic '{topic}': {e}")

    async def _message_handler(self, nats_msg):
        """Internal handler for NATS messages."""
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

    def _extract_message_payload_ids(
        self, payload: Any
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extracts the top-level 'id' and nested 'messageId' (if available) from the payload.
        Handles dict or JSON string payloads gracefully.
        Returns a tuple: (id, messageId) -- either or both may be None.
        """
        payload_dict = {}
        if isinstance(payload, dict):
            payload_dict = payload
        else:
            try:
                payload_dict = json.loads(payload)
            except Exception:
                payload_dict = {}

        id_ = payload_dict.get("id")
        message_id = None
        try:
            params = payload_dict.get("params", {})
            message = params.get("message", {})
            message_id = message.get("messageId")
        except Exception:
            message_id = None

        return id_, message_id

    # Callbacks and error handling
    async def error_cb(self, e):
        logger.error(f"NATS error: {e}")

    async def closed_cb(self):
        logger.warning("Connection to NATS is closed.")

    async def disconnected_cb(self):
        logger.warning("Disconnected from NATS.")

    async def reconnected_cb(self):
        logger.info(f"Reconnected to NATS at {self._nc.connected_url.netloc}...")
