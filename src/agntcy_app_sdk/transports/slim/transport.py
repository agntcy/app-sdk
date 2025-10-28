# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0
import traceback
from typing import Optional, Callable, List
import os
import asyncio
from uuid import uuid4
import inspect
import datetime
import slim_bindings
from slim_bindings import (PyName, PySession, PyMessageContext, )
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
            from ioa_observe.sdk.instrumentations.slim import SLIMInstrumentor

            # SLIMInstrumentor().instrument()
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
            logger.warning("SLIM transport cannot be closed without a SLIM instance.")
            return

        # handle slim server disconnection
        try:
            print(f"slim id: {self._slim.id}, slim id_str: {self._slim.id_str}", f"disconnecting from {self._endpoint}")
            await self._slim.disconnect(self._endpoint)
            logger.info("SLIM transport disconnected")
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

        logger.info(f"Setting up SLIM transport to {self.type()}")
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
        self,
        remote_name: PyName,
        message: Message,
        timeout: float = 30.0,
        **kwargs,
    ) -> None:
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        if not (message and remote_name):
            logger.error(f"message and topic are not provided")
            return None

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
        except Exception as e:
            logger.warning(f"Failed to publish message: {e}")
            return None
        # finally:
        #     logger.debug(f"Closing session {session.id} from _request_first()")
        #     await self._session_manager.close_session(session)

        reply = Message.deserialize(reply)

        return reply

    async def _request_all(
        self,
        remote_name: PyName,
        message: Message,
        recipients: List[str] = None,
        timeout: Optional[float] = 30.0,
        **kwargs,
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
        except Exception as e:
            logger.warning(f"Unhandled exception: {e}.")
            return []

    # send out the end-chat message
    async def _request_group(
        self,
        remote_name: PyName,
        message: Message,
        recipients: List[str] = None,
        end_message: str = "done",
        timeout: float = 60.0,
        **kwargs,
    ) -> List[Message]:
        if not self._slim:
            logger.warning("SLIM client is not initialized, calling setup() ...")
            await self.setup()

        if not recipients:
            raise ValueError(
                "recipients list must be provided for SLIM COLLECT_ALL mode."
            )

        logger.debug(f"Requesting group response from topic: {remote_name}")

        # Convert recipients to PyName objects
        invitees = [self.build_pyname(recipient) for recipient in recipients]

        if not message.headers:
            message.headers = {}

        # Signal to the receiver that they should respond to the group
        message.headers["x-respond-to-group"] = "true"
        # Optionally include an end message to signal to receivers they can close the session
        end_signal = uuid4().hex
        message.headers["x-session-end-message"] = end_signal

        responses = []
        try:
            async with asyncio.timeout(timeout):
                (
                    _,
                    group_session,
                ) = await self._session_manager.group_broadcast_session(
                    remote_name, invitees
                )

                # Give the session a moment to be fully established on the SLIM dataplane- arbitrary delay
                await asyncio.sleep(0.5)

                # Initiate the group broadcast
                await group_session.publish(message.serialize())

                # Wait for responses from invitees until the end message is received
                while True:
                    try:
                        _, msg = await group_session.get_message()
                        deserialized_msg = Message.deserialize(msg)
                        responses.append(deserialized_msg)

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
                    logger.debug(f"Closing group session from _request_group(): {group_session.id}")
                    await self._session_manager.close_session(group_session,
                                                              end_signal=end_signal)
                except Exception as e:
                    logger.error(f"Failed to close session {group_session.id}: {e}")

        return responses

    async def _collect_all(
        self,
        channel: PyName,
        message: Message,
        invitees: List[PyName],
    ) -> List[Message]:
        if not self._slim:
            raise ValueError("SLIM client is not set, please call setup() first.")

        logger.debug(f"Publishing to topic: {channel} for all invitees")

        _, group_session = await self._session_manager.group_broadcast_session(
            channel, invitees
        )

        if not message.headers:
            message.headers = {}

        # Signal to the receiver that we expect a direct response from each invitee
        message.headers["x-respond-to-source"] = "true"

        await group_session.publish(message.serialize())

        # wait for responses from all invitees or be interrupted by caller
        responses = []
        while len(responses) < len(invitees):
            try:
                _, msg = await group_session.get_message()
                msg = Message.deserialize(msg)
                responses.append(msg)
            except Exception as e:
                logger.error(
                    f"Error receiving message on session {group_session.id}: {e}"
                )
                continue

        logger.debug(f"Closing group session from _collect_all(): {group_session.id}")
        await self._session_manager.close_session(group_session)

        return responses

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
                    session = await self._slim.listen_for_session()
                    logger.info(
                        f"Received new session with id: {session.id}, "
                        f"type: {session.session_type}, "
                        f"destination: {session.dst}"
                    )

                    task = asyncio.create_task(
                        self._handle_session_receive(session)
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

    # TODO: resolve this
    async def _handle_session_receive(self, session: PySession) -> None:
        """Handle message receiving for a specific session."""
        consecutive_errors = 0
        max_retries = 3

        try:
            while not self._shutdown_event.is_set():
                logger.info(f"Handling session receive with id: {session.id}")
                try:
                    msg_ctx, msg = await session.get_message()
                    consecutive_errors = 0  # Reset on success
                    end_session = await self._process_received_message(session, msg_ctx, msg)
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
        except Exception as e:
            logger.error(f"Agent {self.local_name}: Unhandled exception in _handle_session_receive for session {session.id}: {e}")
            # Ensure session is closed even on unhandled exceptions
            await self._session_manager.close_session(session)

    async def _process_received_message(self, session: PySession, msg_ctx: PyMessageContext,
                                        msg: bytes) -> bool:
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
            if inspect.iscoroutinefunction(self._callback):
                output = await self._callback(deserialized_msg)
            else:
                output = self._callback(deserialized_msg)
        except Exception as e:
            logger.error(f"Error in callback function: {e}")
            return False

        if output is None:
            logger.info("Received empty output from callback, skipping response.")
            return False

        # Handle response logic
        await self._handle_response(session, msg_ctx, deserialized_msg, output)
        return False

    async def _handle_response(self, session: PySession, msg_ctx: PyMessageContext,
                               original_msg: Message, output: Message) -> None:
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
