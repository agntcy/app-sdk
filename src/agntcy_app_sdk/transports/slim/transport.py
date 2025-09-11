# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable, List, Dict
import os
import asyncio
import inspect
import datetime
import slim_bindings
from slim_bindings import (
    PyName,
)
from .common import (
    create_local_app,
    split_id,
)
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport, ResponseMode, Message
from agntcy_app_sdk.transports.slim.session_manager import SessionManager

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
        shared_secret_identity: str = "slim-mls-secret",
        tls_insecure: bool = True,
        jwt: str = None,
        bundle: str | None = None,
        audience: list[str] | None = None,
    ) -> None:
        if not routable_name:
            raise ValueError(
                "routable_name must be provided in the form 'org/namespace/local_name'"
            )
        if not endpoint:
            raise ValueError(
                "SLIM dataplane endpoint must be provided for SLIMTransport"
            )

        try:
            org, namespace, local_name = routable_name.split("/", 2)
            self.pyname = self.build_pyname(routable_name)
        except ValueError:
            raise ValueError(
                "routable_name must be in the form 'org/namespace/local_name'"
            )
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
        self._tls_insecure = tls_insecure
        self._jwt = jwt
        self._bundle = bundle
        self._audience = audience

        self.enable_opentelemetry = False

        # keep track of topics we are "subscribed" to, will be used to filter incoming
        # messages in the receive loop
        self.active_subscription_topics: Dict[str, PyName] = {}

        self._session_manager = SessionManager()

        self.session_ids = {}

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
    def from_client(cls, client, name: str = None) -> "SLIMTransport":
        """
        Create a SLIM transport instance from an existing SLIM client.
        :param client: An instance of slim_bindings.Slim
        :param name: Optional routable name in the form 'org/namespace/local_name'
        """
        if not isinstance(client, slim_bindings.Slim):
            raise TypeError(f"Expected a SLIM instance, got {type(client)}")

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
        shared_secret_identity = kwargs.get("shared_secret_identity", "slim-mls-secret")
        jwt = kwargs.get("jwt", None)

        if not jwt and not shared_secret_identity:
            logger.warning("No JWT or shared_secret_identity provided, using defaults.")

        return cls(routable_name=name, endpoint=endpoint, **kwargs)

    def type(self) -> str:
        """Return the transport type."""
        return "SLIM"

    async def close(self) -> None:
        if not self._slim:
            return

        # handle slim server disconnection
        self._slim.disconnect(self._endpoint)

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = handler

    async def setup(self):
        """
        Start the async receive loop for incoming messages.
        """
        if self._slim:
            return

        await self._slim_connect()
        await self._receive()

    def build_pyname(
        self, topic: str, org: Optional[str] = None, namespace: Optional[str] = None
    ) -> PyName:
        """
        Build a PyName object from a topic string, optionally using provided org and namespace.
        If org or namespace are not provided, use the transport's local org and namespace.
        """
        topic = self.sanitize_topic(topic)

        if org and namespace:
            org = self.sanitize_topic(org)
            namespace = self.sanitize_topic(namespace)
            return PyName(org, namespace, topic)

        try:
            return split_id(topic)
        except ValueError:
            return PyName(self.org, self.namespace, topic)
        except Exception as e:
            logger.error(f"Error building PyName from topic '{topic}': {e}")
            raise

    async def publish(
        self,
        topic,
        message: Message,
    ) -> None:
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        raise NotImplementedError(
            "publish method is not yet implemented, not consumed by any protocols"
        )

    async def request(
        self,
        topic: str,
        message: Message,
        response_mode: ResponseMode = ResponseMode.FIRST,
        timeout: Optional[float] = 60.0,
        **kwargs,
    ) -> Optional[Message]:
        """Publish a message to a topic."""
        topic = self.sanitize_topic(topic)
        remote_name = self.build_pyname(topic)

        match response_mode:
            case ResponseMode.FIRST:
                return await self._request_first(
                    remote_name=remote_name, message=message, timeout=timeout, **kwargs
                )
            case ResponseMode.COLLECT_N:
                raise NotImplementedError(
                    "COLLECT_N response mode is not yet implemented."
                )
            case ResponseMode.COLLECT_ALL:
                return await self._request_all(
                    remote_name=remote_name, message=message, timeout=timeout, **kwargs
                )
            case ResponseMode.GROUP:
                return await self._request_group(
                    remote_name=remote_name, message=message, timeout=timeout, **kwargs
                )
            case _:
                raise ValueError(f"Unknown response mode: {response_mode}")

    async def _request_first(
        self, remote_name: PyName, message: Message, timeout: float = 30.0
    ) -> None:
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        logger.debug(f"Requesting response from topic: {remote_name}")

        async with self._slim:
            await self._slim.set_route(remote_name)

            # create or get a request-reply (sticky fire-and-forget) session
            _, session = await self._session_manager.request_reply_session()

            if not message.headers:
                message.headers = {}

            message.headers["respond-to-source"] = "true"

            try:
                _, reply = await self._slim.request_reply(
                    session,
                    message.serialize(),
                    remote_name,
                    timeout=datetime.timedelta(seconds=timeout),
                )
            except asyncio.TimeoutError:
                logger.warning(f"Request timed out after {timeout} seconds")
                return None

            reply = Message.deserialize(reply)
            return reply

    async def _request_all(
        self,
        remote_name: PyName,
        message: Message,
        recipients: List[str] = None,
        timeout: Optional[float] = 30.0,
    ) -> List[Message]:
        """
        Send a message to topic and wait for a response from all recipients
        or until the timeout is reached.
        """
        if not recipients:
            raise ValueError(
                "recipients list must be provided for SLIM COLLECT_ALL mode."
            )

        logger.info(
            f"Sending message to topic: {remote_name} and waiting for {len(recipients)} responses"
        )

        # convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in recipients]

        try:
            responses = await asyncio.wait_for(
                self._collect_all(
                    channel=remote_name,
                    message=message,
                    invitees=invitees,
                ),
                timeout=timeout,
            )
            return responses
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )
            return []

    async def _request_group(
        self,
        remote_name: PyName,
        message: Message,
        recipients: List[str] = None,
        end_message: str = "done",
        timeout: float = 60.0,
    ) -> List[Message]:
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        if not recipients:
            raise ValueError(
                "recipients list must be provided for SLIM COLLECT_ALL mode."
            )

        logger.debug(f"Requesting group response from topic: {remote_name}")

        # convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in recipients]

        try:
            responses = await asyncio.wait_for(
                self._collect_until_done(
                    channel=remote_name,
                    message=message,
                    invitees=invitees,
                    end_message=end_message,
                ),
                timeout=timeout,
            )
            return responses
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )
            return []

    async def _collect_all(
        self,
        channel: PyName,
        message: Message,
        invitees: List[PyName],
    ) -> List[Message]:
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"Publishing to topic: {channel} for all invitees")

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
                try:
                    _, msg = await self._slim.receive(session=session_info.id)
                    msg = Message.deserialize(msg)
                    responses.append(msg)
                except Exception as e:
                    logger.error(
                        f"Error receiving message on session {session_info.id}: {e}"
                    )
                    continue

            return responses

    async def _collect_until_done(
        self,
        channel: PyName,
        message: Message,
        invitees: List[PyName],
        end_message: str = "done",
    ) -> List[Message]:
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"Publishing to topic: {channel} for group invitees")

        _, session_info = await self._session_manager.group_broadcast_session(
            channel, invitees
        )

        if not message.headers:
            message.headers = {}

        # Signal to the receiver that they should respond to the group
        message.headers["respond-to-group"] = "true"

        async with self._slim:
            # initiate the group broadcast
            await self._slim.publish(session_info, message.serialize(), channel)

            # wait for responses from invitees until we receive the end_message
            responses = []
            while True:
                try:
                    _, msg = await self._slim.receive(session=session_info.id)
                    msg = Message.deserialize(msg)
                    if end_message in msg.payload:
                        logger.debug("Received end message, stopping collection.")
                        break
                    responses.append(msg)
                except Exception as e:
                    logger.error(
                        f"Error receiving message on session {session_info.id}: {e}"
                    )
                    continue

            return responses

    async def subscribe(self, topic: str, org=None, namespace=None) -> None:
        """
        Store the subscription information for a given topic, org, and namespace
        to be used for receive filtering.
        """
        topic = self.sanitize_topic(topic)

        sub_pyname = self.build_pyname(topic, org, namespace)
        self.active_subscription_topics[sub_pyname.id] = sub_pyname

    def can_receive(self, session_destination: PyName) -> bool:
        """
        Determine if the transport can receive messages for the given session destination.
        """
        for active_sub in self.active_subscription_topics:
            logger.debug(
                f"Checking if can receive: {active_sub} == {session_destination.id}"
            )

        return True

    async def _receive(self) -> None:
        """Main receive method that sets up the background session listener."""
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        asyncio.create_task(self._listen_for_sessions())

    async def _listen_for_sessions(self) -> None:
        """Background task that listens for new sessions and spawns handlers."""
        async with self._slim:
            while True:
                try:
                    session_info, _ = await self._slim.receive()
                    # Spawn a new task to handle this session
                    asyncio.create_task(self._handle_session_receive(session_info.id))
                except Exception as e:
                    logger.error(f"Error receiving session info: {e}")
                    continue

    async def _handle_session_receive(self, session_id: str) -> None:
        """Handle message receiving for a specific session."""
        while True:
            try:
                session, msg = await self._slim.receive(session=session_id)
            except Exception as e:
                logger.error(f"Error receiving message on session {session_id}: {e}")
                continue

            # Process the received message
            await self._process_received_message(session, msg)

    async def _process_received_message(self, session, msg) -> None:
        """Process a single received message and handle response logic."""
        # Check if we should receive messages for this destination
        if not self.can_receive(session.destination_name):
            logger.debug(
                f"Filtering out message for destination: {session.destination_name}"
            )
            return

        if msg is None:
            logger.debug("Received empty message, skipping.")
            return

        # Deserialize the message
        try:
            deserialized_msg = Message.deserialize(msg)
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            return

        # Call the callback function
        try:
            if inspect.iscoroutinefunction(self._callback):
                output = await self._callback(deserialized_msg)
            else:
                output = self._callback(deserialized_msg)
        except Exception as e:
            logger.error(f"Error in callback function: {e}")
            return

        if output is None:
            logger.info("Received empty output from callback, skipping response.")
            return

        # Handle response logic
        await self._handle_response(session, deserialized_msg, output)

    async def _handle_response(self, session, original_msg, output) -> None:
        """Handle response publishing based on message headers."""
        try:
            payload = output.serialize()

            respond_to_source = (
                original_msg.headers.get("respond-to-source", "false").lower() == "true"
            )
            respond_to_group = (
                original_msg.headers.get("respond_to_group", "false").lower() == "true"
            )

            if respond_to_source:
                await self._slim.publish_to(session, payload)
                logger.debug("Response sent to source")
            elif respond_to_group:
                await self._slim.publish(session, payload, session.destination_name)
                logger.debug(f"Response sent to group: {session.destination_name}")
            else:
                logger.debug("No response required based on message headers")

        except Exception as e:
            logger.error(f"Error handling response: {e}")

    async def _slim_connect(
        self,
    ) -> None:
        if self._slim:
            return  # Already connected

        self._slim: slim_bindings.Slim = await create_local_app(
            self.pyname,
            slim={
                "endpoint": self._endpoint,
                "tls": {"insecure": self._tls_insecure},
            },
            enable_opentelemetry=self.enable_opentelemetry,
            shared_secret=self._shared_secret_identity,
            jwt=self._jwt,
            bundle=self._bundle,
            audience=self._audience,
        )

        self._session_manager.set_slim(self._slim)

    def sanitize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for SLIM."""
        # SLIM topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
