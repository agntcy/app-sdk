# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable, List, Dict
import os
import asyncio
import inspect
import datetime
import uuid
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport, Message
import slim_bindings
from slim_bindings import (
    PyName,
    PySessionInfo,
    PySessionConfiguration,
    PySessionDirection,
)
from .common import (
    create_local_app,
    split_id,
)

configure_logging()
logger = get_logger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, PySessionInfo] = {}
        self._slim = None

    def set_slim(self, slim: slim_bindings.Slim):
        """
        Set the SLIM client instance for the session manager.
        """
        self._slim = slim

    async def request_reply_session(
        self,
        max_retries: int = 5,
        timeout: datetime.timedelta = datetime.timedelta(seconds=5),
    ):
        """
        Create a new request-reply session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        # check if we already have a request-reply session
        session_key = "RequestReply"
        if session_key in self._sessions:
            logger.info(f"Reusing existing session: {session_key}")
            return session_key, self._sessions[session_key]

        session = await self._slim.create_session(
            PySessionConfiguration.FireAndForget(
                max_retries=max_retries,
                timeout=timeout,
                sticky=True,
                mls_enabled=False,
            )
        )
        session_key = "RequestReply"
        self._sessions[session_key] = session
        return session_key, session

    async def group_broadcast_session(
        self,
        channel: PyName,
        invitees: list[PyName],
        max_retries: int = 20,
        timeout: datetime.timedelta = datetime.timedelta(seconds=60),
    ):
        """
        Create a new group broadcast session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        # check if we already have a group broadcast session for this channel and invitees
        session_key = f"GroupChannel:{channel}:" + ",".join(
            [str(invitee) for invitee in invitees]
        )
        if session_key in self._sessions:
            logger.info(f"Reusing existing group broadcast session: {session_key}")
            return session_key, self._sessions[session_key]

        session_info = await self._slim.create_session(
            PySessionConfiguration.Streaming(
                PySessionDirection.BIDIRECTIONAL,
                topic=channel,
                moderator=True,
                max_retries=max_retries,
                timeout=timeout,
                mls_enabled=True,
            )
        )

        for invitee in invitees:
            await self._slim.set_route(invitee)
            await self._slim.invite(session_info, invitee)

        # store the session info
        self._sessions[session_key] = session_info
        return session_key, session_info

    def close_session(self, session_key: str):
        """
        Close and remove a session by its key.
        """
        session = self._sessions.pop(session_key, None)
        if session:
            logger.info(f"Closing session: {session_key}")


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

        self._session_manager = SessionManager()

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

        resp = await self._request(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
            message=message,
        )

        if respond:
            return resp

    async def broadcast(
        self,
        topic: str,
        message: Message,
        participants: List[str],
        timeout: Optional[float] = 30.0,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        topic = self.santize_topic(topic)

        logger.info(
            f"Broadcasting to topic: {topic} and waiting for {len(participants)} responses"
        )

        # Generate a unique reply_to topic if not provided
        if not message.reply_to:
            message.reply_to = uuid.uuid4().hex

        try:
            responses = await asyncio.wait_for(
                self._group_broadcast(
                    org=self._default_org,
                    namespace=self._default_namespace,
                    broadcast_topic=topic,
                    message=message,
                    participants=participants,
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

        await self._receive(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
        )

        logger.info(
            f"Subscribed to {self._default_org}/{self._default_namespace}/{topic}"
        )

    # ###################################################
    # SLIM Transport Internal Methods
    # ###################################################

    async def _broadcast(
        self,
        org: str,
        namespace: str,
        broadcast_topic: str,
        message: Message,
        participants: List[str],
    ) -> None:
        if not self._slim:
            await self._slim_connect(org, namespace, uuid.uuid4().hex)

        logger.debug(f"Publishing to topic: {broadcast_topic}")

        channel = PyName(org, namespace, broadcast_topic)
        invitees = []
        for participant in participants:
            if "/" not in participant:
                invitees.append(PyName(org, namespace, participant))
            else:
                try:
                    invitee = split_id(participant)
                    invitees.append(invitee)
                except ValueError:
                    logger.warning(f"Invalid participant ID: {participant}")
        if not invitees:
            logger.error("No valid participants to invite for broadcast.")
            return []
        _, session_info = await self._session_manager.group_broadcast_session(
            channel, invitees
        )

        async with self._slim:
            await self._slim.publish(session_info, message.serialize(), channel)

            # wait for responses from all invitees or be interrupted by caller
            responses = []
            while len(responses) < len(invitees):
                _, msg = await self._slim.receive(session=session_info.id)
                msg = Message.deserialize(msg)
                responses.append(msg)

            return responses

    async def _request(
        self,
        org: str,
        namespace: str,
        topic: str,
        message: Message,
    ) -> None:
        if not self._slim:
            await self._slim_connect(org, namespace, uuid.uuid4().hex)

        logger.debug(f"Publishing to topic: {topic}")

        async with self._slim:
            # Set a slim route to this topic, enabling outbound messages to this topic
            remote_name = split_id(f"{org}/{namespace}/{topic}")
            await self._slim.set_route(remote_name)

            # create or get a request-reply (sticky fire-and-forget) session
            _, session = await self._session_manager.request_reply_session()

            _, reply = await self._slim.request_reply(
                session,
                message.serialize(),
                remote_name,
                timeout=datetime.timedelta(seconds=5),
            )

            reply = Message.deserialize(reply)
            return reply

    async def _receive(self, org: str, namespace: str, topic: str) -> None:
        if not self._slim:
            await self._slim_connect(org, namespace, topic)

        async def background_task():
            async with self._slim:
                while True:
                    # Receive the message from the session
                    session_info, _ = await self._slim.receive()

                    async def inner_task(session_id):
                        while True:
                            # Receive the message from the session
                            session, msg = await self._slim.receive(session=session_id)
                            msg = Message.deserialize(msg)

                            reply_to = msg.reply_to
                            msg.reply_to = None  # we will handle replies instead of the bridge receiver

                            if inspect.iscoroutinefunction(self._callback):
                                output = await self._callback(msg)
                            else:
                                output = self._callback(msg)

                            if reply_to:
                                payload = output.serialize()
                                await self._slim.publish_to(session, payload)

                    asyncio.create_task(inner_task(session_info.id))

        asyncio.create_task(background_task())

    async def _slim_connect(
        self,
        org: str,
        namespace: str,
        topic: str,
    ) -> None:
        # create new gateway object
        logger.info(
            f"Creating new gateway for org: {org}, namespace: {namespace}, topic: {topic}"
        )

        self._slim: slim_bindings.Slim = await create_local_app(
            PyName(org, namespace, topic),
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

        self._session_manager.set_slim(self._slim)

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
