# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable, List, AsyncIterator, Any, Awaitable
import asyncio
import copy
import datetime
import warnings
import slim_bindings
from slim_bindings import Name, Session, MessageContext

from .common import (
    get_or_create_slim_instance,
    split_id,
)
from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.common.tracing import SLIM_INSTRUMENTOR, instrument_if_ready
from agntcy_app_sdk.transport.base import BaseTransport, accepts_publish_fn
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.session_manager import SessionManager

from agntcy_app_sdk.common.auth import add_identity_header

logger = get_logger(__name__)

# UniFFI attaches error variants at import time, so they are invisible to mypy.
_SlimReceiveError: type[slim_bindings.SlimError] = getattr(
    slim_bindings.SlimError, "ReceiveError"
)
_SlimSessionError: type[slim_bindings.SlimError] = getattr(
    slim_bindings.SlimError, "SessionError"
)
_SlimTimeout: type[slim_bindings.SlimError] = getattr(
    slim_bindings.SlimError, "Timeout"
)

# Substrings of SessionError messages that mean the session no longer exists.
_SESSION_GONE = (
    "session channel closed",
    "session closed",
    "session already closed",
    "receive channel closed",
    "session not found",
)

# Consecutive receive errors tolerated on a session, and the pause between them.
_RECEIVE_RETRIES = 3
_RECEIVE_BACKOFF = 0.5

"""
SLIM implementation of the BaseTransport interface.
"""


def _raise_if_past(deadline: float) -> None:
    # ``asyncio.timeout_at`` only fires while the block is suspended, so a
    # reply that is already queued would slip past an expired deadline.
    if asyncio.get_running_loop().time() >= deadline:
        raise TimeoutError


def _session_gone(error: Exception) -> bool:
    return isinstance(error, _SlimSessionError) and any(
        gone in str(error).lower() for gone in _SESSION_GONE
    )


async def _receive_reply(
    session: Session, session_id: int, deadline: float, timeout: float
) -> Message:
    """The next decodable message on a client group session.

    A payload that cannot be decoded is logged and skipped.

    Raises:
        TimeoutError: *deadline* passed.
        slim_bindings.SlimError: the session is gone, or receiving failed
            more than ``_RECEIVE_RETRIES`` times in a row.
    """
    errors = 0
    while True:
        _raise_if_past(deadline)
        try:
            async with asyncio.timeout_at(deadline):
                received = await session.get_message_async(
                    timeout=datetime.timedelta(seconds=timeout)
                )
        except TimeoutError:
            raise
        except Exception as e:
            if _session_gone(e):
                logger.warning(f"Session {session_id} closed while receiving: {e}")
                raise
            errors += 1
            if errors > _RECEIVE_RETRIES:
                logger.error(
                    f"Max retries exceeded receiving on session {session_id}: {e}"
                )
                raise
            logger.warning(
                f"Error receiving message on session {session_id} "
                f"(attempt {errors}/{_RECEIVE_RETRIES}): {e}"
            )
            async with asyncio.timeout_at(deadline):
                await asyncio.sleep(_RECEIVE_BACKOFF)
            continue
        try:
            return Message.deserialize(received.payload)
        except Exception as e:
            logger.error(f"Skipping undecodable message on session {session_id}: {e}")


class SLIMTransport(BaseTransport):
    """
    SLIM Transport implementation using the slim_bindings library.
    """

    TRANSPORT_TYPE: str = "SLIM"
    """Registry key used by :class:`AgntcyFactory`."""

    def __init__(
        self,
        routable_name: str | None = None,
        slim_service: slim_bindings.Service | None = None,
        slim_app: slim_bindings.App | None = None,
        slim_connection_id: int | None = None,
        endpoint: Optional[str] = None,
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=60),
        message_retries: int = 2,
        shared_secret_identity: str | None = None,
        tls_insecure: bool = True,
        jwt: str | None = None,
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
        except ValueError:
            raise ValueError(
                "routable_name must be in the form 'org/namespace/local_name'"
            )
        # Name encrypts the components so we need to store the original values separately.
        # These must be assigned before build_name() which may fall back to self.org/namespace.
        self.org = org
        self.namespace = namespace
        self.local_name = local_name
        self.name = self.build_name(routable_name)
        self._endpoint = endpoint
        self._slim_service = slim_service
        self._slim_app = slim_app
        self._slim_connection_id = slim_connection_id

        self._callback: Callable[..., Awaitable[Any]] | None = None
        self._callback_takes_publish_fn = False
        self.message_timeout = message_timeout
        self.message_retries = message_retries
        self._shared_secret_identity = shared_secret_identity
        self._tls_insecure = tls_insecure
        self._jwt = jwt
        self._bundle = bundle
        self._audience = audience

        self._session_manager = SessionManager()
        if slim_app is not None:
            if slim_connection_id is None:
                raise ValueError(
                    "slim_connection_id is required when slim_app is given"
                )
            self._session_manager.set_slim(slim_app, slim_connection_id)
        self._tasks: set[asyncio.Task[None]] = set()
        self._listener_task: asyncio.Task[None] | None = None
        self._shutdown_event = asyncio.Event()

        instrument_if_ready("SLIMTransport", SLIM_INSTRUMENTOR)

        logger.debug(f"SLIMTransport initialized with endpoint: {endpoint}")

    @property
    def enable_opentelemetry(self) -> bool:
        """Deprecated. Always False; use ``tracing_requested()`` instead."""
        warnings.warn(
            "SLIMTransport.enable_opentelemetry is deprecated and always False. "
            "Use agntcy_app_sdk.common.tracing.tracing_requested() instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        return False

    # -----------------------------------------------------------------------------
    # BaseTransport method implementations
    # -----------------------------------------------------------------------------

    # -----------------------------------------------------------------------------
    # Point-to-Point
    # -----------------------------------------------------------------------------
    async def send(self, recipient: str, message: Message, **kwargs: Any) -> None:
        """
        Send a message to a single recipient without expecting a response.
        """
        raise NotImplementedError

    async def request(
        self, recipient: str, message: Message, timeout: int = 6, **kwargs: Any
    ) -> Message:
        """
        Send a message to a recipient and await a single response.
        """
        topic = self.sanitize_topic(recipient)
        remote_name = self.build_name(topic)

        if not self._slim_app:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()
        if self._slim_app is None or self._slim_connection_id is None:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"Requesting response from topic: {remote_name}")

        await self._slim_app.set_route_async(remote_name, self._slim_connection_id)

        # create a point-to-point session
        point_to_point_session = await self._session_manager.point_to_point_session(
            remote_name, timeout=datetime.timedelta(seconds=timeout)
        )

        if not message.headers:
            message.headers = {}
        message.headers["x-respond-to-source"] = "true"

        try:
            await point_to_point_session.publish_async(message.serialize(), None, None)
            logger.debug(
                f"Published message to {remote_name}, now waiting for response."
            )
            # Wait for reply from remote peer
            reply = await point_to_point_session.get_message_async(
                timeout=datetime.timedelta(seconds=timeout)
            )
            logger.debug(f"Received message back from {remote_name}")
        except asyncio.TimeoutError:
            logger.warning(f"Request timed out after {timeout} seconds")
            raise
        except Exception:
            logger.exception("Failed to publish message in p2p session")
            raise
        finally:
            logger.debug(
                f"Closing point-to-point session: {point_to_point_session.session_id()} "
            )
            await self._session_manager.close_session(point_to_point_session)

        return Message.deserialize(reply.payload)

    async def request_stream(
        self, recipient: str, message: Message, timeout: int = 90, **kwargs: Any
    ) -> AsyncIterator[Message]:
        """
        Send a request and receive a stream of responses over a single
        SLIM point-to-point session.

        The session stays open across multiple ``get_message_async()``
        calls and yields each response as it arrives.  The caller is
        responsible for terminating iteration (e.g. by breaking out of
        the ``async for`` loop); the ``finally`` block ensures the
        session is always closed.
        """
        topic = self.sanitize_topic(recipient)
        remote_name = self.build_name(topic)

        if not self._slim_app:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()
        if self._slim_app is None or self._slim_connection_id is None:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"request_stream: opening session to {remote_name}")

        await self._slim_app.set_route_async(remote_name, self._slim_connection_id)

        session = await self._session_manager.point_to_point_session(
            remote_name, timeout=datetime.timedelta(seconds=timeout)
        )

        if not message.headers:
            message.headers = {}
        message.headers["x-respond-to-source"] = "true"

        # Read once: SLIM does not allow reading properties of a closed session.
        session_id = session.session_id()
        try:
            await session.publish_async(message.serialize(), None, None)
            logger.debug(
                f"request_stream: published to {remote_name}, waiting for stream."
            )

            while True:
                reply_raw = await session.get_message_async(
                    timeout=datetime.timedelta(seconds=timeout)
                )
                reply = Message.deserialize(reply_raw.payload)
                logger.debug(f"request_stream: received message type={reply.type}")
                yield reply
        except (asyncio.TimeoutError, _SlimTimeout) as e:
            logger.warning(f"request_stream timed out after {timeout}s")
            if isinstance(e, TimeoutError):
                raise
            raise TimeoutError(f"request_stream timed out after {timeout}s") from e
        except Exception:
            logger.exception("Failed in request_stream")
            raise
        finally:
            logger.debug(f"Closing request_stream session: {session_id}")
            try:
                await self._session_manager.close_session(session)
            except Exception as e:
                logger.error(f"Failed to close session {session_id}: {e}")

    # -----------------------------------------------------------------------------
    # Fan-Out / Publish-Subscribe
    # -----------------------------------------------------------------------------

    async def publish(self, topic: str, message: Message, **kwargs: Any) -> None:
        """
        Publish a message to all subscribers of the topic.
        """
        raise NotImplementedError

    async def gather(
        self,
        topic: str,
        message: Message,
        recipients: List[str],
        message_limit: int | None = None,
        timeout: float = 60,
        **kwargs: Any,
    ) -> List[Message]:
        """
        Publish a message and collect responses from multiple subscribers.
        """
        if message_limit is None:
            message_limit = len(recipients)

        responses: List[Message] = []
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
        message_limit: int | None = None,
        timeout: float = 60,
        **kwargs: Any,
    ) -> AsyncIterator[Message]:
        """
        Publish a message and yield responses from multiple subscribers as they arrive.
        """
        if not self._slim_app:
            raise ValueError("SLIM client is not set, please call setup() first.")

        topic = self.sanitize_topic(topic)
        remote_name = self.build_name(topic)

        if not recipients:
            raise ValueError(
                "recipients list must be provided for SLIM COLLECT_ALL mode."
            )

        # convert recipients to Name objects
        invitees = [self.build_name(recipient) for recipient in recipients]

        # Headers below go on a copy, not on the caller's message.
        message = copy.copy(message)
        message.headers = dict(message.headers or {})

        limit: float = float("inf") if message_limit is None else message_limit

        logger.debug(
            f"Broadcasting to topic: {remote_name} and waiting for {limit} responses"
        )

        # The deadline wraps each await, never a ``yield``: an
        # ``asyncio.timeout`` held across ``yield`` cancels the consumer's own
        # code between events, and the consumer gets ``CancelledError``
        # instead of ``TimeoutError``.
        deadline = asyncio.get_running_loop().time() + timeout
        group_session: Session | None = None
        session_id: int | None = None
        try:
            async with asyncio.timeout_at(deadline):
                _, group_session = await self._session_manager.group_broadcast_session(
                    remote_name, invitees
                )
                session_id = group_session.session_id()

                await asyncio.sleep(0.5)

                # Signal to the receiver that we expect a direct response from each invitee
                message.headers["x-respond-to-source"] = "true"
                await add_identity_header(message.headers)

                logger.debug("Publishing message to topic: %s", topic)

                await group_session.publish_async(message.serialize(), None, None)

                logger.debug(
                    "Published message to topic: %s via session %s, now waiting for responses",
                    topic,
                    session_id,
                )

            # wait for responses from all invitees or be interrupted by caller
            messages_received = 0
            while messages_received < limit:
                reply = await _receive_reply(
                    group_session, session_id, deadline, timeout
                )
                messages_received += 1
                yield reply
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )
            raise
        finally:
            if group_session:
                logger.debug(
                    f"Closing group session {session_id} after gathering responses"
                )
                try:
                    await self._session_manager.close_session(group_session)
                except Exception as e:
                    logger.error(f"Failed to close session {session_id}: {e}")

    # -----------------------------------------------------------------------------
    # Group Chat / Multi-Party Conversation
    # -----------------------------------------------------------------------------

    async def start_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str = "done",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> List[Message]:
        """
        Create a new conversation including the given participants.
        """
        responses: List[Message] = []
        async for msg in self.start_streaming_conversation(
            group_channel, participants, init_message, end_message, timeout, **kwargs
        ):
            responses.append(msg)
        return responses

    async def start_streaming_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str = "done",
        timeout: float = 60.0,
        **kwargs: Any,
    ) -> AsyncIterator[Message]:
        """
        Create a new conversation including the given participants.
        """
        if not self._slim_app:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        remote_name = self.build_name(group_channel)

        if not participants:
            raise ValueError(
                "participants list must be provided for SLIM COLLECT_ALL mode."
            )

        logger.debug(f"Requesting group response from topic: {remote_name}")

        # Convert recipients to Name objects
        invitees = [self.build_name(recipient) for recipient in participants]

        # Headers below go on a copy, not on the caller's message.
        init_message = copy.copy(init_message)
        init_message.headers = dict(init_message.headers or {})

        # Signal to the receiver that they should respond to the group
        init_message.headers["x-respond-to-group"] = "true"
        await add_identity_header(init_message.headers)

        # See gather_stream: the deadline must not be held across ``yield``.
        deadline = asyncio.get_running_loop().time() + timeout
        group_session: Session | None = None
        session_id: int | None = None
        try:
            async with asyncio.timeout_at(deadline):
                (
                    _,
                    group_session,
                ) = await self._session_manager.group_broadcast_session(
                    remote_name, invitees
                )
                session_id = group_session.session_id()

                # Give the session a moment to be fully established on the SLIM dataplane- arbitrary delay
                await asyncio.sleep(0.5)

                # Initiate the group broadcast
                await group_session.publish_async(init_message.serialize(), None, None)

            # Wait for responses from invitees until the end message is received
            while True:
                deserialized_msg = await _receive_reply(
                    group_session, session_id, deadline, timeout
                )
                yield deserialized_msg

                # Check for end message to stop collection
                if end_message in str(deserialized_msg.payload):
                    break
        except asyncio.TimeoutError:
            logger.warning(
                f"Broadcast to topic {remote_name} timed out after {timeout} seconds"
            )
            raise
        finally:
            if group_session:
                try:
                    await self._session_manager.close_session(group_session)
                except Exception as e:
                    logger.error(f"Failed to close session {session_id}: {e}")

    # -----------------------------------------------------------------------------
    # Utilities and setup methods
    # -----------------------------------------------------------------------------

    @classmethod
    def from_client(cls, client: Any, name: str | None = None) -> "SLIMTransport":
        """
        Create a SLIM transport instance from an existing SLIM client.
        :param client: An instance of slim_bindings.App
        :param name: Optional routable name in the form 'org/namespace/local_name'
        """
        if not isinstance(client, slim_bindings.App):
            raise TypeError(f"Expected a SLIM instance, got {type(client)}")

        raise NotImplementedError("from_client method is not yet implemented")

    @classmethod
    def from_config(
        cls, endpoint: str, name: str | None = None, **kwargs: Any
    ) -> "SLIMTransport":
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
        return self.TRANSPORT_TYPE

    async def close(self) -> None:
        if not self._slim_service or self._slim_connection_id is None:
            logger.warning("SLIM client is not initialized, no connection close.")
            return

        # handle slim server disconnection
        try:
            # blocking operation
            self._slim_service.disconnect(self._slim_connection_id)

        except Exception as e:
            if "connection not found" in str(e).lower():
                # Silence benign "connection not found" errors;
                pass
            else:
                logger.error(f"Error disconnecting SLIM transport: {e}")

    def set_callback(self, handler: Callable[..., Awaitable[Any]]) -> None:
        """Set the message handler function."""
        self._callback = handler
        self._callback_takes_publish_fn = accepts_publish_fn(handler)

        # Start the listener task after setting the callback
        if not self._slim_app:
            raise ValueError("SLIM client is not set, please call setup() first.")
        self._listener_task = asyncio.create_task(self._listen_for_sessions())

    async def setup(self) -> None:
        """
        Start the async receive loop for incoming messages.
        """
        instrument_if_ready("SLIMTransport", SLIM_INSTRUMENTOR)
        if self._slim_app:
            return

        await self._slim_connect()

    def build_name(
        self, topic: str, org: Optional[str] = None, namespace: Optional[str] = None
    ) -> Name:
        """
        Build a Name object from a topic string, optionally using provided org and namespace.
        If org or namespace are not provided, use the transport's local org and namespace.
        """
        topic = self.sanitize_topic(topic)

        if org and namespace:
            org = self.sanitize_topic(org)
            namespace = self.sanitize_topic(namespace)
            return Name(org, namespace, topic)

        try:
            return split_id(topic)
        except ValueError:
            return Name(self.org, self.namespace, topic)
        except Exception as e:
            logger.error(f"Error building Name from topic '{topic}': {e}")
            raise

    async def subscribe(
        self, topic: str, org: str | None = None, namespace: str | None = None
    ) -> None:
        """
        Store the subscription information for a given topic, org, and namespace
        to be used for receive filtering.
        """
        logger.debug(
            "SLIMTransport.subscribe is a no-op since SLIM does not require explicit subscriptions."
        )

    async def _listen_for_sessions(self) -> None:
        """Background task that listens for new sessions and spawns handlers."""
        try:
            while not self._shutdown_event.is_set():
                try:
                    if self._slim_app is None:
                        raise ValueError(
                            "SLIM client is not set, please call setup() first."
                        )
                    received_session = await self._slim_app.listen_for_session_async(
                        timeout=self.message_timeout
                    )
                    logger.debug(
                        f"Received new session with id: {received_session.session_id()}, "
                        f"type: {received_session.session_type()}, "
                        f"destination: {received_session.destination()},"
                    )
                    task = asyncio.create_task(
                        self._handle_session_receive(received_session)
                    )
                    self._tasks.add(task)
                    task.add_done_callback(self._tasks.discard)
                except asyncio.CancelledError:
                    raise
                except _SlimReceiveError as e:
                    if "timed out" in str(e).lower():
                        logger.debug("listen_for_session timed out (no new sessions)")
                        continue
                    else:
                        logger.error(f"ReceiveError while listening for session: {e}")
                        await asyncio.sleep(1)
                except Exception as e:
                    logger.error(f"Error receiving session info: {e}")
                    await asyncio.sleep(1)  # prevent busy loop
        except asyncio.CancelledError:
            logger.debug("Listener cancelled")
            raise

    async def _handle_session_receive(self, session: Session) -> None:
        """Handle message receiving for a specific session."""
        consecutive_errors = 0
        max_retries = _RECEIVE_RETRIES
        session_id = session.session_id()

        try:
            while not self._shutdown_event.is_set():
                try:
                    received_msg = await session.get_message_async(None)
                    consecutive_errors = 0  # Reset on success
                    await self._process_received_message(session, received_msg)
                except asyncio.CancelledError:
                    raise
                except _SlimSessionError as e:
                    if _session_gone(e):
                        logger.debug(
                            f"Session {session_id} closed remotely (likely by moderator), stopping listener"
                        )
                        break
                    else:
                        consecutive_errors += 1
                        if consecutive_errors > max_retries:
                            logger.error(
                                f"Max retries exceeded for session {session_id}, closing: {e}"
                            )
                            await self._session_manager.close_session(session)
                            break
                        logger.error(
                            f"Unexpected session error while getting messages: {e}"
                        )
                        await asyncio.sleep(_RECEIVE_BACKOFF)
                except Exception as e:
                    logger.error(f"Error receiving messages: {e}")

                    consecutive_errors += 1
                    if consecutive_errors > max_retries:
                        logger.error(
                            f"Max retries exceeded for session {session_id}, closing: {e}"
                        )
                        # also close the session
                        await self._session_manager.close_session(session)
                        break
                    logger.warning(
                        f"Error receiving message on session {session_id} (attempt {consecutive_errors}/{max_retries}): {e}"
                    )
                    await asyncio.sleep(_RECEIVE_BACKOFF)
        except asyncio.CancelledError:
            logger.debug(f"Session {session_id} handler cancelled")
            raise
        finally:
            logger.debug(f"Session {session_id} receive loop terminated")

    async def _process_received_message(
        self, session: Session, msg: slim_bindings.ReceivedMessage
    ) -> None:
        """Process a single received message and handle response logic."""
        # Deserialize the message
        try:
            deserialized_msg = Message.deserialize(msg.payload)
        except Exception as e:
            logger.error(f"Failed to deserialize message: {e}")
            return

        # Build a closure that publishes intermediate messages back to the
        # client over the same session.  This is passed as ``publish_fn``
        # to the callback so that streaming handlers can emit status
        # updates before the final response.
        async def _publish_intermediate(intermediate_msg: Message) -> None:
            try:
                await session.publish_to_async(
                    msg.context, intermediate_msg.serialize(), None, None
                )
            except Exception as pub_err:
                logger.error(f"Error publishing intermediate message: {pub_err}")

        # Call the callback function
        if self._callback is None:
            logger.error("Error in callback function: no callback set")
            return
        try:
            if self._callback_takes_publish_fn:
                output = await self._callback(
                    deserialized_msg, publish_fn=_publish_intermediate
                )
            else:
                output = await self._callback(deserialized_msg)
        except Exception as e:
            logger.error(f"Error in callback function: {e}")
            return

        if output is None:
            logger.debug("Received empty output from callback, skipping response.")
            return

        # Handle response logic
        await self._handle_response(session, msg.context, deserialized_msg, output)

    async def _handle_response(
        self,
        session: Session,
        msg_ctx: MessageContext,
        original_msg: Message,
        output: Message,
    ) -> None:
        """Handle response publishing based on message headers."""
        session_id = session.session_id()

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

            # propagate relevant headers from the original message if not already set
            if "x-respond-to-source" not in output.headers:
                output.headers["x-respond-to-source"] = original_msg.headers.get(
                    "x-respond-to-source", "false"
                )
            if "x-respond-to-group" not in output.headers:
                output.headers["x-respond-to-group"] = original_msg.headers.get(
                    "x-respond-to-group", "false"
                )

            payload = output.serialize()

            if respond_to_source:
                logger.debug(f"Responding to source on channel: {session.source()}")
                await session.publish_to_async(msg_ctx, payload, None, None)
            elif respond_to_group:
                logger.debug(
                    f"Responding to group on channel: {session.destination()} with payload:\n {output}"
                )
                await session.publish_async(payload, None, None)
            else:
                logger.warning("No response required based on message headers")

        except Exception as e:
            msg = str(e)
            if "session not found" in msg:
                # Silence benign "session not found" errors; they are transient SLIM-side errors.
                # TODO: Revisit with SLIM team if this still exists in 0.5.0
                logger.debug(f"Error handling response: {e}")
            elif "session closed" in msg:
                logger.warn(
                    f"Unable to process incoming message, session {session_id} closed remotely (likely by moderator)"
                )
            else:
                logger.error(f"Error handling response: {e}")

    async def _slim_connect(
        self,
    ) -> None:
        if self._slim_app:
            return  # Already connected

        slim_service, slim_app, connection_id = await get_or_create_slim_instance(
            self.name,
            slim_endpoint=self._endpoint,
            slim_insecure_client=self._tls_insecure,
            shared_secret=self._shared_secret_identity,
            jwt=self._jwt,
            bundle=self._bundle,
            audience=self._audience,
        )
        self._slim_service = slim_service
        self._slim_app = slim_app
        self._slim_connection_id = connection_id
        self._session_manager.set_slim(slim_app, connection_id)

    def sanitize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for SLIM."""
        # SLIM topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic
