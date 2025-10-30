# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable, List, AsyncIterator
import os
import asyncio
from uuid import uuid4
import datetime
import slim_bindings
from slim_bindings import PyName, PySession
from slim_bindings._slim_bindings import PyMessageContext

from .common import (
    create_local_app,
    split_id,
)
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.session_manager import SessionManager

configure_logging()
logger = get_logger(__name__)

"""
SLIM implementation of the BaseTransport interface.
"""

# Global SLIM client instance
slim = None


async def get_global_slim_instance(
    pyname,
    endpoint,
    tls_insecure,
    shared_secret_identity,
    jwt,
    bundle,
    audience,
    enable_opentelemetry,
):
    global slim

    # Return existing instance if already created
    if slim is not None:
        return slim

    slim = await create_local_app(
        pyname,
        slim={
            "endpoint": endpoint,
            "tls": {"insecure": tls_insecure},
        },
        enable_opentelemetry=enable_opentelemetry,
        shared_secret=shared_secret_identity,
        jwt=jwt,
        bundle=bundle,
        audience=audience,
    )

    return slim


class SLIMTransport(BaseTransport):
    """
    SLIM Transport implementation using the slim_bindings library.
    """

    def __init__(
        self,
        routable_name: str = None,
        slim_instance=None,
        endpoint: Optional[str] = None,
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=60),
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

        self._session_manager = SessionManager()
        self._tasks: set[asyncio.Task] = set()
        self._listener_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()

        self.enable_opentelemetry = False
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            # Initialize tracing if enabled

            # TODO: upgrade ioa sdk and uncomment this line
            # SLIMInstrumentor().instrument()
            logger.info("SLIMTransport initialized with tracing enabled")

        logger.info(f"SLIMTransport initialized with endpoint: {endpoint}")

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
        raise NotImplementedError

    async def request(
        self, recipient: str, message: Message, timeout: int = 6, **kwargs
    ) -> Message:
        """
        Send a message to a recipient and await a single response.
        """
        topic = self.sanitize_topic(recipient)
        remote_name = self.build_pyname(topic)

        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        logger.debug(f"Requesting response from topic: {remote_name}")

        await self._slim.set_route(remote_name)

        # create or get a point-to-point session
        _, session = await self._session_manager.point_to_point_session(
            remote_name, timeout=datetime.timedelta(seconds=timeout)
        )

        if not message.headers:
            message.headers = {}
        message.headers["x-respond-to-source"] = "true"

        try:
            await session.publish(message.serialize())
            # Wait for reply from remote peer
            _, reply = await session.get_message()
        except asyncio.TimeoutError:
            logger.warning(f"Request timed out after {timeout} seconds")
            return None
        except Exception as e:
            logger.warning(f"Failed to publish message: {e}")
            return None

        reply = Message.deserialize(reply)
        return reply

    async def request_stream(
        self, recipient: str, message: Message, timeout: int = 90, **kwargs
    ) -> AsyncIterator[Message]:
        """
        Send a request and receive a continuous stream of responses.
        """
        raise NotImplementedError("Streaming not supported for SLIM point-to-point.")

    # -----------------------------------------------------------------------------
    # Fan-Out / Publish-Subscribe
    # -----------------------------------------------------------------------------

    async def publish(self, topic: str, message: Message, **kwargs) -> None:
        """
        Publish a message to all subscribers of the topic.
        """
        raise NotImplementedError

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
        async for msg in self.gather_stream(
            topic,
            message,
            recipients,
            message_limit=message_limit,
            timeout=timeout,
            **kwargs,
        ):
            responses.append(msg)
        return responses

    async def gather_stream(
        self,
        topic: str,
        message: Message,
        recipients: List[str],
        message_limit: int = None,
        timeout: int = 60,
        **kwargs,
    ) -> AsyncIterator[Message]:
        """
        Publish a message and yield responses from multiple subscribers as they arrive.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        topic = self.sanitize_topic(topic)
        remote_name = self.build_pyname(topic)

        if not recipients:
            raise ValueError(
                "recipients list must be provided for SLIM COLLECT_ALL mode."
            )

        # convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in recipients]

        if message_limit is None:
            message_limit = float("inf")

        logger.debug(
            f"Broadcasting to topic: {remote_name} and waiting for {message_limit} responses"
        )

        try:
            async with asyncio.timeout(timeout):
                _, group_session = await self._session_manager.group_broadcast_session(
                    remote_name, invitees
                )

                # Signal to the receiver that we expect a direct response from each invitee
                if not message.headers:
                    message.headers = {}
                message.headers["x-respond-to-source"] = "true"

                await group_session.publish(message.serialize())

                # wait for responses from all invitees or be interrupted by caller
                messages_received = 0
                while messages_received < message_limit:
                    try:
                        _, msg = await group_session.get_message()
                        msg = Message.deserialize(msg)
                        messages_received += 1
                        yield msg
                    except Exception as e:
                        logger.error(
                            f"Error receiving message on session {group_session.id}: {e}"
                        )
                        continue
                # TODO: close session properly with SLIM v0.7.0: https://github.com/agntcy/slim/issues/780
                # await self._session_manager.close_session(group_session)
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )

    # -----------------------------------------------------------------------------
    # Group Chat / Multi-Party Conversation
    # -----------------------------------------------------------------------------

    async def start_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str = "done",
        muted_participants: List[str] = [],
        timeout: float = 60.0,
        **kwargs,
    ) -> List[Message]:
        """
        Create a new conversation including the given participants.
        """
        responses = []
        async for msg in self.start_streaming_conversation(
            group_channel,
            participants,
            init_message,
            end_message,
            muted_participants=muted_participants,
            timeout=timeout,
            **kwargs,
        ):
            responses.append(msg)

        return responses

    async def start_streaming_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str = "done",
        muted_participants: List[str] = [],
        timeout: float = 60.0,
        **kwargs,
    ) -> AsyncIterator[Message]:
        """
        Create a new conversation including the given participants.
        """
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        remote_name = self.build_pyname(group_channel)

        if not participants:
            raise ValueError(
                "participants list must be provided for SLIM COLLECT_ALL mode."
            )

        logger.debug(f"Requesting group response from topic: {remote_name}")

        # Convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in participants]
        muted_participants = [
            str(self.build_pyname(recipient)) for recipient in muted_participants
        ]

        if not init_message.headers:
            init_message.headers = {}

        # Signal to the receiver that they should respond to the group
        init_message.headers["x-respond-to-group"] = "true"
        # Optionally include an end message to signal to receivers they can close the session
        end_signal = uuid4().hex
        init_message.headers["x-session-end-message"] = end_signal
        init_message.headers["x-muted-participants"] = ",".join(muted_participants)

        group_session = None
        try:
            async with asyncio.timeout(timeout):
                (
                    _,
                    group_session,
                ) = await self._session_manager.group_broadcast_session(
                    remote_name, invitees
                )

                # Give the session a moment to be fully established on the SLIM dataplane- arbitrary delay
                await asyncio.sleep(1)

                # Initiate the group broadcast
                await group_session.publish(init_message.serialize())

                # Wait for responses from invitees until the end message is received
                while True:
                    try:
                        _, msg = await group_session.get_message()
                        deserialized_msg = Message.deserialize(msg)
                        yield deserialized_msg

                        # Check for end message to stop collection
                        if end_message in str(deserialized_msg.payload):
                            break
                    except Exception as e:
                        logger.warning(
                            f"Issue encountered while receiving message on session {group_session.id}: {e}"
                        )
                        continue
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )
        finally:
            if group_session:
                try:
                    await self._session_manager.close_session(
                        group_session, remote=remote_name, end_signal=end_signal
                    )
                except Exception as e:
                    logger.error(f"Failed to close session: {e}")

    # -----------------------------------------------------------------------------
    # Utilities and setup methods
    # -----------------------------------------------------------------------------

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
        try:
            await self._slim.disconnect(self._endpoint)
        except Exception as e:
            if "connection not found" in str(e).lower():
                # Silence benign "connection not found" errors;
                pass
            else:
                logger.error(f"Error disconnecting SLIM transport: {e}")

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = handler

        # Start the listener task after setting the callback
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")
        self._listener_task = asyncio.create_task(self._listen_for_sessions())

    async def setup(self):
        """
        Start the async receive loop for incoming messages.
        """
        if self._slim:
            return

        await self._slim_connect()

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

    async def subscribe(self, topic: str, org=None, namespace=None) -> None:
        """
        Store the subscription information for a given topic, org, and namespace
        to be used for receive filtering.
        """
        logger.warning(
            "SLIMTransport.subscribe is a no-op since SLIM does not require explicit subscriptions."
        )

    async def _listen_for_sessions(self) -> None:
        """Background task that listens for new sessions and spawns handlers."""
        try:
            while not self._shutdown_event.is_set():
                try:
                    received_session = await self._slim.listen_for_session()
                    logger.debug(
                        f"Received new session: {received_session.id} - {received_session.dst}"
                    )

                    task = asyncio.create_task(
                        self._handle_session_receive(received_session)
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Error receiving session info: {e}")
                    await asyncio.sleep(1)  # prevent busy loop
        except asyncio.CancelledError:
            logger.info("Listener cancelled")
            raise

    async def _handle_session_receive(self, session: PySession) -> None:
        """Handle message receiving for a specific session."""
        consecutive_errors = 0
        max_retries = 3

        try:
            while not self._shutdown_event.is_set():
                try:
                    msg_ctx, msg = await session.get_message()
                    consecutive_errors = 0  # Reset on success
                    end_session = await self._process_received_message(
                        session, msg_ctx, msg
                    )
                    if end_session:
                        logger.info(
                            f"Ending session {session.id} as requested by client"
                        )
                        await self._session_manager.close_session(session)
                        break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    consecutive_errors += 1
                    if consecutive_errors > max_retries:
                        logger.error(
                            f"Max retries exceeded for session {session.id}, closing: {e}"
                        )
                        # also close the session
                        await self._session_manager.close_session(session)
                        break
                    logger.warning(
                        f"Error receiving message on session {session.id} (attempt {consecutive_errors}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(0.5)  # backoff to avoid spin
        except asyncio.CancelledError:
            logger.info(f"Session {session.id} handler cancelled")
            raise

    async def _process_received_message(
        self, session: PySession, msg_ctx: PyMessageContext, msg: bytes
    ) -> bool:
        """Process a single received message and handle response logic."""
        # Deserialize the message
        try:
            deserialized_msg = Message.deserialize(msg)
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            return False

        end_msg = deserialized_msg.headers.get("x-session-end-message", "")
        if end_msg != "" and end_msg in str(deserialized_msg.payload):
            logger.info(f"Received end message {end_msg}, closing session {session.id}")
            return True  # Signal to end the session

        # Call the callback function
        try:
            output = await self._callback(deserialized_msg)
        except Exception as e:
            logger.error(f"Error in callback function: {e}")
            return False

        if output is None:
            logger.info("Received empty output from callback, skipping response.")
            return False

        # Handle response logic
        await self._handle_response(session, msg_ctx, deserialized_msg, output)
        return False

    async def _handle_response(
        self,
        session: PySession,
        msg_ctx: PyMessageContext,
        original_msg,
        output: Message,
    ) -> None:
        """Handle response publishing based on message headers."""
        try:
            respond_to_source = (
                original_msg.headers.get("x-respond-to-source", "false").lower()
                == "true"
            )
            respond_to_group = (
                original_msg.headers.get("x-respond-to-group", "false").lower()
                == "true"
            )

            if not output.headers:
                output.headers = {}

            # check if we are muted
            muted_participants = original_msg.headers.get("x-muted-participants", "")
            if str(self.pyname) in muted_participants.split(","):
                logger.info(
                    f"Not responding on session {session.id} because transport is muted."
                )
                return

            # propagate relevant headers from the original message if not already set
            if "x-respond-to-source" not in output.headers:
                output.headers["x-respond-to-source"] = original_msg.headers.get(
                    "x-respond-to-source", "false"
                )
            if "x-respond-to-group" not in output.headers:
                output.headers["x-respond-to-group"] = original_msg.headers.get(
                    "x-respond-to-group", "false"
                )
            if "x-session-end-message" not in output.headers:
                output.headers["x-session-end-message"] = original_msg.headers.get(
                    "x-session-end-message", ""
                )
            if "x-muted-participants" not in output.headers:
                output.headers["x-muted-participants"] = original_msg.headers.get(
                    "x-muted-participants", ""
                )

            payload = output.serialize()

            if respond_to_source:
                logger.debug(f"Responding to source on channel: {session.src}")
                await session.publish_to(msg_ctx, payload)
            elif respond_to_group:
                logger.debug(
                    f"Responding to group on channel: {session.dst} with payload:\n {output}"
                )
                await session.publish(payload)
            else:
                logger.warning("No response required based on message headers")

        except Exception as e:
            msg = str(e)
            if "session not found" in msg:
                # Silence benign "session not found" errors; they are transient SLIM-side errors.
                # TODO: Revisit with SLIM team if this still exists in 0.5.0
                logger.debug(f"Error handling response: {e}")
            else:
                logger.error(f"Error handling response: {e}")

    async def _slim_connect(
        self,
    ) -> None:
        if self._slim:
            return  # Already connected

        self._slim = await get_global_slim_instance(
            self.pyname,
            self._endpoint,
            self._tls_insecure,
            self._shared_secret_identity,
            self._jwt,
            self._bundle,
            self._audience,
            self.enable_opentelemetry,
        )

        self._session_manager.set_slim(self._slim)

    def sanitize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for SLIM."""
        # SLIM topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
