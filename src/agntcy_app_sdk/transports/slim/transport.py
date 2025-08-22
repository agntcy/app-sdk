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
from agntcy_app_sdk.transports.slim.session_manager import SessionManager
import slim_bindings
from slim_bindings import (
    PyName,
)
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
        routable_name: str = None,
        slim_instance=None,
        endpoint: Optional[str] = None,
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=10),
        message_retries: int = 2,
        shared_secret_identity: str = "slim-secret-change-me",
    ) -> None:
        if not routable_name:
            raise ValueError(
                "routable_name must be provided in the form 'org/namespace/local_name'"
            )
        if not endpoint:
            raise ValueError(
                "SLIM dataplane endpoint must be provided for SLIMTransport"
            )

        org, namespace, local_name = routable_name.split("/", 2)
        self.pyname = PyName(org, namespace, local_name)
        # PyName encrypts the components so we need to store the original values separately
        self.org = org
        self.namespace = namespace
        self.local_name = local_name
        self._endpoint = endpoint
        self._slim = slim_instance

        self._callback = None
        self.message_timeout = message_timeout
        self.message_retries = message_retries
        self._shared_secret_identity = shared_secret_identity
        self.enable_opentelemetry = False

        # keep track of topics we are "subscribed" to, will be used to filter incoming
        # messages in the receive loop
        self.active_subscription_topics: Dict[str, PyName] = {}

        self._session_manager = SessionManager()

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            # Initialize tracing if enabled
            self.enable_opentelemetry = True
            from ioa_observe.sdk.instrumentations.slim import SLIMInstrumentor

            SLIMInstrumentor().instrument()
            logger.info("SLIMTransport initialized with tracing enabled")

        logger.info(f"SLIMTransport initialized with endpoint: {endpoint}")

    # ###################################################
    # BaseTransport interface methods
    # ###################################################

    @classmethod
    def from_client(cls, client, name: str = None) -> "SLIMTransport":
        """
        Create a SLIM transport instance from an existing SLIM client.
        :param client: An instance of slim_bindings.Slim
        :param name: Optional routable name in the form 'org/namespace/local_name'
        """
        if not isinstance(client, slim_bindings.Slim):
            raise TypeError(f"Expected a SLIM instance, got {type(client)}")

        # TODO: get local_name from client
        raise NotImplementedError("from_client method is not yet implemented")

    @classmethod
    def from_config(cls, endpoint: str, name: str, **kwargs) -> "SLIMTransport":
        """
        Create a SLIM transport instance from a configuration.
        :param endpoint: The SLIM server endpoint.
        :param routable_name: The routable name in the form 'org/namespace/local_name'.
        :param kwargs: Additional configuration parameters.
        """
        if not name:
            raise ValueError(
                "Routable name must be provided in the form 'org/namespace/local_name'"
            )

        return cls(routable_name=name, endpoint=endpoint, **kwargs)

    def type(self) -> str:
        """Return the transport type."""
        return "SLIM"

    async def close(self) -> None:
        pass

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = handler

    async def setup(self):
        """
        Start the async receive loop for incoming messages.
        """
        await self._slim_connect()
        await self._receive()

    def build_pyname(
        self, topic: str, org: Optional[str] = None, namespace: Optional[str] = None
    ) -> PyName:
        """
        Build a PyName object from a topic string, optionally using provided org and namespace.
        If org or namespace are not provided, use the transport's local org and namespace.
        """
        if org and namespace:
            return PyName(org, namespace, topic)

        try:
            return split_id(topic)
        except ValueError:
            logger.warning(
                f"org or namespace not provided, using locals: {self.org}, {self.namespace}"
            )
            return PyName(self.org, self.namespace, topic)
        except Exception as e:
            logger.error(f"Error building PyName from topic '{topic}': {e}")
            raise

    async def publish(
        self,
        topic: str,
        message: Message,
        org: Optional[str] = None,
        namespace: Optional[str] = None,
        respond: Optional[bool] = False,
    ) -> Optional[Message]:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)

        remote_name = self.build_pyname(topic, org, namespace)

        logger.info(f"Publishing {message.payload} to topic: {topic}")

        # if we are asked to provide a response, use or generate a reply_to topic
        if respond and not message.reply_to:
            message.reply_to = uuid.uuid4().hex

        resp = await self._request(
            remote_name=remote_name,
            message=message,
        )

        if respond:
            return resp

    async def broadcast(
        self,
        topic: str,
        message: Message,
        recipients: List[str],
        timeout: Optional[float] = 30.0,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        topic = self.santize_topic(topic)

        logger.info(
            f"Broadcasting to topic: {topic} and waiting for {len(recipients)} responses"
        )

        print(f"[debug] Recipients: {recipients}")

        # convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in recipients]

        try:
            responses = await asyncio.wait_for(
                self._broadcast(
                    channel=self.build_pyname(topic),
                    message=message,
                    invitees=invitees,
                ),
                timeout=timeout,
            )
            return responses
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {topic} timed out after {timeout} seconds"
            )
            return []

    async def subscribe(self, topic: str, org=None, namespace=None) -> None:
        """
        Store the subscription information for a given topic, org, and namespace
        to be used for receive filtering.
        """
        topic = self.santize_topic(topic)

        sub_pyname = self.build_pyname(topic, org, namespace)
        self.active_subscription_topics[sub_pyname.id] = sub_pyname

    # ###################################################
    # SLIM Transport Internal Methods
    # ###################################################

    async def _broadcast(
        self,
        channel: PyName,
        message: Message,
        invitees: List[PyName],
    ) -> List[Message]:
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"Publishing to topic: {channel}")

        _, session_info = await self._session_manager.group_broadcast_session(
            channel, invitees
        )

        if not message.headers:
            message.headers = {}

        # Signal to the receiver that we expect a direct response from each invitee
        message.headers["respond-to-source"] = "true"

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
        remote_name: PyName,
        message: Message,
    ) -> None:
        if not self._slim:
            raise ValueError(
                "SLIM client is not initialized, please call setup() first."
            )

        logger.debug(f"Publishing to topic: {remote_name}")

        async with self._slim:
            await self._slim.set_route(remote_name)

            # create or get a request-reply (sticky fire-and-forget) session
            _, session = await self._session_manager.request_reply_session()

            if not message.headers:
                message.headers = {}

            # the transport receiver can handle both request-reply and group-chat,
            # signal we want a direct reply
            message.headers["respond-to-source"] = "true"

            _, reply = await self._slim.request_reply(
                session,
                message.serialize(),
                remote_name,
                timeout=datetime.timedelta(seconds=5),
            )

            reply = Message.deserialize(reply)
            return reply

    def can_receive(self, session_destination: PyName) -> bool:
        """
        Determine if the transport can receive messages for the given session destination.
        """
        for active_sub in self.active_subscription_topics:
            print(
                "sub id:", active_sub, "session_destination id:", session_destination.id
            )

    async def _receive(self) -> None:
        if not self._slim:
            raise ValueError(
                "SLIM client is not initialized, please call setup() first."
            )

        async def background_task():
            async with self._slim:
                while True:
                    # Receive the message from the session
                    session_info, _ = await self._slim.receive()

                    async def inner_task(session_id):
                        while True:
                            # Receive the message from the session
                            session, msg = await self._slim.receive(session=session_id)

                            self.can_receive(session.destination_name)

                            msg = Message.deserialize(msg)

                            print(
                                f"Received message on session {session_id}: {msg.headers}"
                            )

                            msg.reply_to = None

                            if inspect.iscoroutinefunction(self._callback):
                                output = await self._callback(msg)
                            else:
                                output = self._callback(msg)

                            if (
                                msg.headers.get("respond-to-source", "false").lower()
                                == "true"
                            ):
                                payload = output.serialize()
                                await self._slim.publish_to(session, payload)
                            elif (
                                msg.headers.get("respond_to_group", "false").lower()
                                == "true"
                            ):
                                payload = output.serialize()
                                await self._slim.publish(
                                    session,
                                    payload,
                                    session.destination_name,
                                )

                    asyncio.create_task(inner_task(session_info.id))

        asyncio.create_task(background_task())

    async def _slim_connect(
        self,
    ) -> None:
        if self._slim:
            return  # Already connected

        self._slim: slim_bindings.Slim = await create_local_app(
            self.pyname,
            slim={
                "endpoint": self._endpoint,
                "tls": {"insecure": True},
            },
            enable_opentelemetry=self.enable_opentelemetry,
            shared_secret=self._shared_secret_identity,
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
