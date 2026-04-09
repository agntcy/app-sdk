# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import os

import nats
from identityservice.sdk import IdentityServiceSdk
from nats.aio.client import Client as NATS

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.message import Message
from typing import Callable, List, Optional, Any, Awaitable, AsyncIterator
from uuid import uuid4

logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""


class NatsTransport(BaseTransport):
    TRANSPORT_TYPE: str = "NATS"
    """Registry key used by :class:`AgntcyFactory`."""

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
        self._ephemeral_subs: dict[str, Any] = {}

        # connection options
        self.connect_timeout = kwargs.get("connect_timeout", 5)
        self.reconnect_time_wait = kwargs.get("reconnect_time_wait", 2)
        self.max_reconnect_attempts = kwargs.get("max_reconnect_attempts", 30)
        self.drain_timeout = kwargs.get("drain_timeout", 2)

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            logger.debug("NatsTransport initialized with tracing enabled")
            from ioa_observe.sdk.instrumentations.nats import NATSInstrumentor

            NATSInstrumentor().instrument()
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

        For a single recipient the message is published directly (no invite
        protocol overhead).  For multiple recipients an ephemeral invite
        protocol is used so that each recipient only needs to be subscribed
        to its own unique name – no shared broadcast topic is required.
        """

        if self._nc is None:
            raise RuntimeError(
                "NATS client is not connected, please call setup() before subscribing"
            )

        if not recipients:
            raise ValueError(
                "recipients list must be provided for NATS COLLECT_ALL mode."
            )

        # -----------------------------------------------------------------
        # Single-recipient: skip invite protocol (backward compat)
        # -----------------------------------------------------------------
        if len(recipients) == 1:
            publish_topic = self.santize_topic(topic)
            reply_topic = uuid4().hex
            message.reply_to = reply_topic

            if is_identity_auth_enabled():
                try:
                    access_token = IdentityServiceSdk().access_token()
                    if access_token:
                        message.headers["Authorization"] = f"Bearer {access_token}"
                except Exception as e:
                    logger.error(f"Failed to get access token for agent: {e}")

            logger.debug(
                f"Publishing to: {publish_topic} and receiving from: {reply_topic}"
            )

            response_queue: asyncio.Queue = asyncio.Queue()
            effective_limit = (
                message_limit if message_limit is not None else float("inf")
            )

            async def _single_response_handler(nats_msg) -> None:
                msg = Message.deserialize(nats_msg.data)
                await response_queue.put(msg)

            sub = None
            try:
                sub = await self._nc.subscribe(reply_topic, cb=_single_response_handler)
                await self.publish(topic, message)

                received = 0
                while received < effective_limit:
                    try:
                        msg = await asyncio.wait_for(
                            response_queue.get(), timeout=timeout
                        )
                        received += 1
                        logger.debug(f"Received {received} response")
                        yield msg
                    except asyncio.TimeoutError:
                        logger.warning(
                            f"Timeout reached after {timeout}s; "
                            f"collected {received} response(s)"
                        )
                        break
            finally:
                if sub is not None:
                    await sub.unsubscribe()
            return

        # -----------------------------------------------------------------
        # Multi-recipient: invite protocol
        # -----------------------------------------------------------------
        ephemeral_topic = uuid4().hex
        reply_topic = uuid4().hex
        ack_topic = uuid4().hex
        message.reply_to = reply_topic

        if is_identity_auth_enabled():
            try:
                access_token = IdentityServiceSdk().access_token()
                if access_token:
                    message.headers["Authorization"] = f"Bearer {access_token}"
            except Exception as e:
                logger.error(f"Failed to get access token for agent: {e}")

        logger.debug(
            f"Invite protocol: ephemeral={ephemeral_topic}, "
            f"reply={reply_topic}, ack={ack_topic}"
        )

        response_queue: asyncio.Queue = asyncio.Queue()
        ack_queue: asyncio.Queue = asyncio.Queue()

        async def _response_handler(nats_msg) -> None:
            msg = Message.deserialize(nats_msg.data)
            await response_queue.put(msg)

        async def _ack_handler(nats_msg) -> None:
            msg = Message.deserialize(nats_msg.data)
            if msg.headers.get("x-nats-invite-type") == "invite_ack":
                await ack_queue.put(msg)

        reply_sub = None
        ack_sub = None
        try:
            reply_sub = await self._nc.subscribe(reply_topic, cb=_response_handler)
            ack_sub = await self._nc.subscribe(ack_topic, cb=_ack_handler)

            # Phase 2: Send invites to each recipient's unique name
            for recipient in recipients:
                invite = Message(
                    type="invite",
                    payload=b"",
                    headers={
                        "x-nats-invite-type": "invite",
                        "x-nats-broadcast-topic": ephemeral_topic,
                        "x-nats-ack-topic": ack_topic,
                    },
                )
                await self.publish(self.santize_topic(recipient), invite)

            # Phase 3: Wait for ACKs
            acks_received = 0
            while acks_received < len(recipients):
                try:
                    await asyncio.wait_for(ack_queue.get(), timeout=10)
                    acks_received += 1
                except asyncio.TimeoutError:
                    logger.warning(
                        f"ACK timeout: got {acks_received}/{len(recipients)}"
                    )
                    break

            # Phase 4: Publish actual message to ephemeral topic
            await self.publish(ephemeral_topic, message)

            # Phase 5: Collect responses
            received = 0
            effective_limit = (
                message_limit if message_limit is not None else float("inf")
            )
            while received < effective_limit:
                try:
                    msg = await asyncio.wait_for(response_queue.get(), timeout=timeout)
                    received += 1
                    yield msg
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Timeout reached after {timeout}s; "
                        f"collected {received} response(s)"
                    )
                    break

            # Phase 6: Teardown
            teardown = Message(
                type="teardown",
                payload=b"",
                headers={
                    "x-nats-invite-type": "teardown",
                    "x-nats-broadcast-topic": ephemeral_topic,
                },
            )
            await self.publish(ephemeral_topic, teardown)
        finally:
            if reply_sub:
                await reply_sub.unsubscribe()
            if ack_sub:
                await ack_sub.unsubscribe()

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
        return self.TRANSPORT_TYPE

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
            logger.debug("Already connected to NATS server")
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
        logger.debug("Connected to NATS server")

    async def close(self) -> None:
        """Close the NATS connection."""
        # Clean up any lingering ephemeral subscriptions
        for _topic, sub in self._ephemeral_subs.items():
            try:
                await sub.unsubscribe()
            except Exception:
                pass
        self._ephemeral_subs.clear()

        if self._nc:
            try:
                await self._nc.drain()
                await self._nc.close()
                logger.debug("NATS connection closed")
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
            logger.debug(f"Subscribed to topic: {topic}")
        except Exception as e:
            logger.error(f"Error subscribe to topic '{topic}': {e}")

    async def _handle_invite(self, message: Message) -> None:
        """Handle an incoming invite message by subscribing to the ephemeral
        broadcast topic and sending an ACK back to the caller."""
        broadcast_topic = message.headers["x-nats-broadcast-topic"]
        ack_topic = message.headers["x-nats-ack-topic"]

        # Subscribe to the ephemeral broadcast topic using existing _message_handler
        sub = await self._nc.subscribe(broadcast_topic, cb=self._message_handler)
        self._ephemeral_subs[broadcast_topic] = sub

        # Send ACK back
        ack = Message(
            type="ack",
            payload=b"",
            headers={"x-nats-invite-type": "invite_ack"},
        )
        await self.publish(ack_topic, ack)

    async def _handle_teardown(self, message: Message) -> None:
        """Handle a teardown message by unsubscribing from the ephemeral
        broadcast topic."""
        broadcast_topic = message.headers.get("x-nats-broadcast-topic", "")
        sub = self._ephemeral_subs.pop(broadcast_topic, None)
        if sub:
            await sub.unsubscribe()

    async def _message_handler(self, nats_msg):
        """
        Internal NATS message handler that deserializes the message and invokes the user-defined callback.
        """
        message = Message.deserialize(nats_msg.data)

        # Add reply_to from NATS message if not in payload
        if nats_msg.reply and not message.reply_to:
            message.reply_to = nats_msg.reply

        # Intercept invite protocol messages
        invite_type = message.headers.get("x-nats-invite-type")
        if invite_type == "invite":
            await self._handle_invite(message)
            return
        if invite_type == "teardown":
            await self._handle_teardown(message)
            return

        # Process the message with the registered handler
        if self._callback:
            # Build publish_fn for intermediate streaming messages
            async def _publish_intermediate(intermediate_msg):
                if message.reply_to:
                    try:
                        await self.publish(message.reply_to, intermediate_msg)
                    except Exception as e:
                        logger.error(f"Error publishing intermediate message: {e}")

            try:
                resp = await self._callback(message, publish_fn=_publish_intermediate)
            except TypeError:
                # Fallback: callback does not accept publish_fn (e.g. tests
                # or custom handlers).  Call without the extra kwarg.
                resp = await self._callback(message)

            if not resp and message.reply_to:
                logger.warning("Handler returned no response for message.")
                err_msg = Message(
                    type="error",
                    payload="No response from handler",
                    reply_to=message.reply_to,
                )
                await self.publish(message.reply_to, err_msg)

            # publish final response to the reply topic
            await self.publish(message.reply_to, resp)

    # Callbacks and error handling
    async def error_cb(self, e):
        logger.error(f"NATS error: {e}")

    async def closed_cb(self):
        logger.warning("Connection to NATS is closed.")

    async def disconnected_cb(self):
        logger.warning("Disconnected from NATS.")

    async def reconnected_cb(self):
        logger.debug(f"Reconnected to NATS at {self._nc.connected_url.netloc}...")
