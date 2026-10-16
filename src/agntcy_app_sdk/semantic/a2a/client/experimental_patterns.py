# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Experimental A2A client for communication patterns beyond the A2A spec.

The standard A2A client handles point-to-point request/response between
two agents. This module extends that model with experimental operations
— broadcast (publish/subscribe) and multi-party group chat — over
non-HTTP transports such as SLIM and NATS.

The client preserves core A2A benefits: AgentCard-based discovery,
JSON-RPC message envelopes, and typed ``SendMessageRequest`` payloads.
Standard A2A operations (send_message, get_task, etc.) delegate to the
inner upstream ``Client``; experimental operations (broadcast_message,
start_groupchat) delegate directly to the underlying ``BaseTransport``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Callable, List
from uuid import uuid4

from a2a.client.client import Client, ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.interceptors import ClientCallInterceptor
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.client.utils import (
    create_rpc_error,
    get_identity_auth_error,
    message_translator,
    run_after_interceptors,
    run_before_interceptors,
)
from agntcy_app_sdk.semantic.a2a.transport_types import SENDER_HEADER
from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)

# Method names as seen by interceptors (identical to the upstream
# ``BaseClient``) and the JSON-RPC (A2A v1) method each maps to on the wire.
_METHOD_SEND = "send_message"
_METHOD_SEND_STREAMING = "send_message_streaming"
_WIRE_METHODS = {
    _METHOD_SEND: "SendMessage",
    _METHOD_SEND_STREAMING: "SendStreamingMessage",
}


@dataclass(frozen=True)
class BroadcastFailure:
    """One broadcast recipient's failed reply.

    Attributes:
        sender: The recipient's identity from the reply's ``x-a2a-sender``
            header, or ``None`` if the server did not send one (older
            servers, or a transport-generated error reply).
        error: What went wrong: the recipient's JSON-RPC error mapped to its
            ``A2AError``, a decoding failure, or an ``after``-interceptor
            failure.
    """

    sender: str | None
    error: Exception


class A2ABroadcastError(A2AClientError):
    """A broadcast did not get a successful reply from every recipient.

    Raised by :meth:`A2AExperimentalClient.broadcast_message` and
    :meth:`A2AExperimentalClient.broadcast_message_streaming` when, by the
    time the call ends, at least one reply was an error or fewer final
    replies arrived than expected.  The successful replies are kept, so a
    partial result is never mistaken for a complete one and never lost.

    Attributes:
        failures: One :class:`BroadcastFailure` per failed reply, in arrival
            order.
        responses: The successful replies.  Empty for the streaming variant,
            whose successful events were already yielded to the caller.
        succeeded: The sender of each successful final reply, in arrival
            order (aligned with ``responses`` for ``broadcast_message``);
            ``None`` where the reply carried no sender identity.
        expected: How many final replies the call waited for.
        received: How many distinct final replies (successful or not)
            arrived.
        missing_recipients: The ``recipients`` that never replied, when that
            can be determined (every reply named a sender that is one of the
            ``recipients``); otherwise empty and only :attr:`missing` is
            known.
        timed_out: ``True`` when the transport raised a timeout before every
            reply arrived (see :class:`A2ABroadcastTimeoutError`).
    """

    def __init__(
        self,
        *,
        failures: List[BroadcastFailure] | None = None,
        responses: List[SendMessageResponse] | None = None,
        succeeded: List[str | None] | None = None,
        expected: int = 0,
        received: int = 0,
        missing_recipients: List[str] | None = None,
        timed_out: bool = False,
    ) -> None:
        self.failures = list(failures or [])
        self.responses = list(responses or [])
        self.succeeded = list(succeeded or [])
        self.expected = expected
        self.received = received
        self.missing_recipients = list(missing_recipients or [])
        self.timed_out = timed_out
        super().__init__(self._describe())

    @property
    def errors(self) -> List[Exception]:
        """The exceptions of :attr:`failures`, in arrival order."""
        return [f.error for f in self.failures]

    @property
    def missing(self) -> int:
        """How many expected final replies never arrived."""
        return max(self.expected - self.received, 0)

    def _describe(self) -> str:
        head = "Broadcast timed out" if self.timed_out else "Broadcast incomplete"
        parts = [f"{head}: {self.received} of {self.expected} replies received"]
        if self.failures:
            detail = "; ".join(
                f"{f.sender or 'unknown sender'}: {type(f.error).__name__}: {f.error}"
                for f in self.failures
            )
            parts.append(f"{len(self.failures)} failed [{detail}]")
        if self.missing:
            names = (
                f" [{', '.join(self.missing_recipients)}]"
                if self.missing_recipients
                else ""
            )
            parts.append(f"{self.missing} missing{names}")
        return "; ".join(parts)


class A2ABroadcastTimeoutError(A2ABroadcastError, TimeoutError):
    """The transport timed out before every broadcast reply arrived.

    Also a ``TimeoutError``, so code that already catches timeouts keeps
    working, and it now carries the partial result as well.
    """


def _reply_sender(raw_resp: Any) -> str | None:
    """The sender identity a reply carries in its ``x-a2a-sender`` header."""
    headers = getattr(raw_resp, "headers", None)
    sender = headers.get(SENDER_HEADER) if isinstance(headers, dict) else None
    return sender if isinstance(sender, str) and sender else None


class _BroadcastTally:
    """Book-keeping for one broadcast call: which senders' finals arrived.

    Finals are counted per sender, so a recipient that answers twice cannot
    make up for one that never answered.  Replies without a sender identity
    (older servers) are counted individually, as before.
    """

    def __init__(self, expected: int, recipients: List[str] | None) -> None:
        self.expected = expected
        self.recipients = list(recipients or [])
        self.responses: List[SendMessageResponse] = []
        self.succeeded: List[str | None] = []
        self.failures: List[BroadcastFailure] = []
        self._seen: set[str] = set()
        self._anonymous = 0

    @property
    def received(self) -> int:
        return len(self._seen) + self._anonymous

    @property
    def done(self) -> bool:
        return self.received >= self.expected

    @property
    def incomplete(self) -> bool:
        return bool(self.failures) or not self.done

    def accept_final(self, sender: str | None) -> bool:
        """Count a final reply; ``False`` for a duplicate from a known sender."""
        if sender is None:
            self._anonymous += 1
            return True
        if sender in self._seen:
            logger.warning(
                f"Ignoring a duplicate final broadcast reply from {sender!r}"
            )
            return False
        self._seen.add(sender)
        return True

    def missing_recipients(self) -> List[str]:
        if self.done or self._anonymous or not self._seen <= set(self.recipients):
            # Complete, or some replies cannot be attributed to a recipient
            # (no sender header, or a server whose topic is not the name the
            # caller used, e.g. a SLIM topic that differs from its app name).
            return []
        return [r for r in self.recipients if r not in self._seen]

    def error(
        self, *, timed_out: bool = False, keep_responses: bool = True
    ) -> A2ABroadcastError:
        cls = A2ABroadcastTimeoutError if timed_out else A2ABroadcastError
        return cls(
            failures=self.failures,
            responses=self.responses if keep_responses else None,
            succeeded=self.succeeded,
            expected=self.expected,
            received=self.received,
            missing_recipients=self.missing_recipients(),
            timed_out=timed_out,
        )


def _stream_response_from_message(resp: SendMessageResponse) -> StreamResponse:
    """Convert a ``SendMessageResponse`` into the equivalent ``StreamResponse``."""
    out = StreamResponse()
    if resp.HasField("task"):
        out.task.CopyFrom(resp.task)
    elif resp.HasField("message"):
        out.message.CopyFrom(resp.message)
    return out


class A2AExperimentalClient(Client):
    """Subclass of the upstream ``a2a.client.Client`` ABC that adds
    experimental transport methods (broadcast, groupchat).

    Standard A2A operations delegate to the inner upstream ``Client``.
    Experimental operations delegate directly to a ``BaseTransport``.

    This class is only returned by the factory when negotiation selects
    a patterns transport (``slimpatterns`` / ``natspatterns``).  The
    ``transport`` and ``topic`` fields are therefore always present.

    Interceptors registered on the client run (``before`` / ``after``) around
    experimental operations just like they do around standard ones.
    """

    def __init__(
        self,
        client: Client,
        agent_card: AgentCard,
        transport: BaseTransport,
        topic: str,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> None:
        super().__init__(interceptors=list(interceptors or []))
        self._client = client
        self._agent_card = agent_card
        self._transport = transport
        self._topic = topic

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agent_card(self) -> AgentCard:
        """The agent card for this client."""
        return self._agent_card

    @property
    def upstream_client(self) -> Client:
        """Access to the underlying upstream ``Client``."""
        return self._client

    @property
    def transport(self) -> BaseTransport:
        """The underlying ``BaseTransport``."""
        return self._transport

    @property
    def topic(self) -> str:
        """The topic used for transport operations."""
        return self._topic

    # ------------------------------------------------------------------
    # Interceptor support
    # ------------------------------------------------------------------

    async def add_interceptor(self, interceptor: ClientCallInterceptor) -> None:
        """Attach an interceptor to this client *and* the inner client."""
        await super().add_interceptor(interceptor)
        await self._client.add_interceptor(interceptor)

    async def _build_message(
        self,
        method: str,
        request: SendMessageRequest,
        context: ClientCallContext | None,
    ) -> Any:
        """Run ``before`` interceptors and wrap *request* as a transport ``Message``.

        *method* is the interceptor-level name (``send_message`` /
        ``send_message_streaming``); it is mapped to the JSON-RPC method.
        """
        if context is None and self._interceptors:
            context = ClientCallContext()
        request, context = await run_before_interceptors(
            self._interceptors,
            method=method,
            request=request,
            agent_card=self._agent_card,
            context=context,
        )
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": _WIRE_METHODS[method],
            "params": MessageToDict(request),
        }
        headers: dict[str, str] = {}
        if context is not None and context.service_parameters:
            headers.update(context.service_parameters)
        return message_translator(request=payload, headers=headers), context

    @staticmethod
    def _is_forbidden(payload: dict[str, Any], status_code: int | None) -> bool:
        """An identity-auth rejection (HTTP 403 / ``"forbidden"``)."""
        return payload.get("error") == "forbidden" or status_code == 403

    @staticmethod
    def _parse_payload(
        payload: dict[str, Any],
        status_code: int | None,
        proto: ProtoMessage,
        *,
        forbidden_as_message: bool = True,
    ) -> ProtoMessage:
        """Parse a raw transport payload (JSON-RPC response) into *proto*.

        Identity-auth rejections become a synthetic agent message when
        *forbidden_as_message* is set, and raise ``A2AClientError``
        otherwise; JSON-RPC errors raise the matching ``A2AError``.
        """
        if A2AExperimentalClient._is_forbidden(payload, status_code):
            if not forbidden_as_message:
                raise A2AClientError("Access forbidden: identity auth failed")
            logger.warning("Received forbidden error in A2A response: %s", payload)
            payload = get_identity_auth_error()
        elif "error" in payload:
            raise create_rpc_error(payload["error"])
        result = payload.get("result", payload)
        return ParseDict(result, proto, ignore_unknown_fields=True)

    async def _read_reply(
        self,
        raw_resp: Any,
        proto: ProtoMessage,
        *,
        method: str,
        context: ClientCallContext | None,
        sender: str | None = None,
    ) -> Any:
        """Decode one transport reply and run the ``after`` interceptors.

        Failures are logged under the right heading and re-raised.  The
        heading is chosen from the payload itself, not from the exception
        type:

        * an identity-auth rejection (HTTP 403 / ``"forbidden"``) is a
          failure, not an answer: ``Broadcast recipient ... refused access``;
        * a JSON-RPC reply carrying an ``error`` member is the *recipient's*
          failure: ``Broadcast recipient ... replied with an error``;
        * anything else that fails (not JSON, not an object, a ``result``
          that does not fit the A2A schema) is an
          ``Error decoding JSON response``;
        * an ``after`` interceptor that raises is reported as such.
        """
        who = repr(sender) if sender else "(unknown sender)"
        try:
            payload = json.loads(raw_resp.payload.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(
                    f"expected a JSON object, got {type(payload).__name__}"
                )
        except Exception as e:
            logger.error(f"Error decoding JSON response from {who}: {e}")
            raise
        try:
            result = self._parse_payload(
                payload, raw_resp.status_code, proto, forbidden_as_message=False
            )
        except Exception as e:
            if self._is_forbidden(payload, raw_resp.status_code):
                logger.warning(f"Broadcast recipient {who} refused access: {e}")
            elif "error" in payload:
                logger.warning(f"Broadcast recipient {who} replied with an error: {e}")
            else:
                logger.error(f"Error decoding JSON response from {who}: {e}")
            raise
        try:
            return await run_after_interceptors(
                self._interceptors,
                method=method,
                result=result,
                agent_card=self._agent_card,
                context=context,
            )
        except Exception as e:
            logger.error(f"After-interceptor failed on the reply from {who}: {e}")
            raise

    # ------------------------------------------------------------------
    # Client ABC — delegate to upstream Client
    # ------------------------------------------------------------------

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[StreamResponse]:
        """Send a message via the upstream client."""
        async for event in self._client.send_message(request, context=context):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieve a task from the upstream client."""
        return await self._client.get_task(request, context=context)

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """List tasks via the upstream client."""
        return await self._client.list_tasks(request, context=context)

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Cancel a task via the upstream client."""
        return await self._client.cancel_task(request, context=context)

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Create push notification config via the upstream client."""
        return await self._client.create_task_push_notification_config(
            request, context=context
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Get push notification config via the upstream client."""
        return await self._client.get_task_push_notification_config(
            request, context=context
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """List push notification configs via the upstream client."""
        return await self._client.list_task_push_notification_configs(
            request, context=context
        )

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Delete push notification config via the upstream client."""
        await self._client.delete_task_push_notification_config(
            request, context=context
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[StreamResponse]:
        """Subscribe to task updates via the upstream client."""
        async for event in self._client.subscribe(request, context=context):
            yield event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Return the agent card (see ``PatternsClientTransport``)."""
        return await self._client.get_extended_agent_card(
            request, context=context, signature_verifier=signature_verifier
        )

    async def close(self) -> None:
        """Close the inner client (and with it the underlying transport)."""
        await self._client.close()

    # ------------------------------------------------------------------
    # Experimental operations — broadcast & groupchat via BaseTransport
    # ------------------------------------------------------------------

    async def broadcast_message(
        self,
        request: SendMessageRequest,
        *,
        recipients: List[str],
        context: ClientCallContext | None = None,
        broadcast_topic: str | None = None,
        timeout: float = 60.0,
    ) -> List[SendMessageResponse]:
        """Broadcast a message to multiple recipients via transport.

        When used with streaming agents the transport may deliver
        intermediate ``A2AStatusUpdate`` messages before the final
        ``A2AResponse``.  This method filters those out and returns
        only the final responses (one per recipient).

        Returns the successful replies only when *every* recipient answered
        successfully.  Otherwise :class:`A2ABroadcastError` is raised once
        the call ends, carrying the successful replies in ``.responses``, the
        failed ones (with their sender) in ``.failures``, and the recipients
        that never answered in ``.missing_recipients``:

        * **Error reply** (a JSON-RPC error, or a payload that cannot be
          decoded): it counts as that recipient's final, so the call does not
          wait for the timeout, and is reported as a failure.
        * **No reply, SLIM:** the transport raises at *timeout*; this becomes
          :class:`A2ABroadcastTimeoutError` (also a ``TimeoutError``).
        * **No reply, NATS:** the transport ends the stream at *timeout*
          without raising, so the client cannot tell a timeout from an early
          end; this becomes a plain :class:`A2ABroadcastError` with
          ``timed_out=False`` and ``.missing > 0``.
        * **Transport failure** (not connected, no *recipients*, publish
          failed): the transport's own exception propagates unchanged.

        Replies are matched to senders through the ``x-a2a-sender`` header
        that SDK servers stamp on every reply.  A second final from the same
        sender is ignored, so one recipient cannot stand in for another.
        Replies without the header (older servers) are counted individually.
        """
        msg, context = await self._build_message(_METHOD_SEND, request, context)

        if not broadcast_topic:
            broadcast_topic = self._topic

        tally = _BroadcastTally(len(recipients), recipients)

        stream = self._transport.gather_stream(
            broadcast_topic,
            msg,
            recipients=recipients,
            timeout=timeout,
        )
        try:
            async for raw_resp in stream:
                # Skip intermediate A2AStatusUpdate messages that streaming
                # agents emit; every other message (including an error
                # reply) is one recipient's final response.
                if raw_resp.type == "A2AStatusUpdate":
                    continue

                sender = _reply_sender(raw_resp)
                if not tally.accept_final(sender):
                    continue
                try:
                    response = await self._read_reply(
                        raw_resp,
                        SendMessageResponse(),
                        method=_METHOD_SEND,
                        context=context,
                        sender=sender,
                    )
                except Exception as e:
                    # A failed final still counts, otherwise we would wait
                    # out the full timeout for a reply that has already
                    # arrived.  It is reported once the call ends.
                    tally.failures.append(BroadcastFailure(sender, e))
                else:
                    tally.responses.append(response)
                    tally.succeeded.append(sender)

                if tally.done:
                    break

            if tally.incomplete:
                raise tally.error()
            return tally.responses
        except A2ABroadcastError:
            # Must precede ``TimeoutError``: A2ABroadcastTimeoutError is one.
            raise
        except TimeoutError as e:
            raise tally.error(timed_out=True) from e
        finally:
            # ``break`` does not close an async generator; close it now so the
            # transport releases its sessions instead of at loop shutdown.
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

    async def broadcast_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        recipients: List[str],
        context: ClientCallContext | None = None,
        broadcast_topic: str | None = None,
        message_limit: int | None = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[StreamResponse]:
        """Broadcast with streaming responses, including intermediate status events.

        Yields a ``StreamResponse`` for every event as it arrives from each
        recipient: intermediate ``status_update`` / ``task`` events, plus one
        final response per recipient.  The stream ends after *message_limit*
        final responses have been received (defaults to ``len(recipients)``).

        Error handling follows :meth:`broadcast_message`, adapted to a stream:

        * An error reply is not yielded, but it does count as that
          recipient's final.  A malformed *intermediate* event is logged and
          skipped; only finals decide the outcome.
        * Once the stream has ended, :class:`A2ABroadcastError` is raised if
          any final failed or fewer finals arrived than expected, so every
          successful event reaches the caller first.  Its ``.responses`` is
          empty (they were already yielded); ``.succeeded`` names their
          senders.
        * A SLIM timeout raises :class:`A2ABroadcastTimeoutError`; a NATS
          timeout ends the stream and raises :class:`A2ABroadcastError`.
        * A transport failure propagates unchanged.
        * If the caller stops iterating early (``break``), the stream is
          closed and no error is raised for replies it did not wait for.

        ``.missing_recipients`` is only filled when *message_limit* asks for
        a reply from every recipient; with a smaller limit there is no
        telling which of the unanswered recipients were "missing".
        """
        msg, context = await self._build_message(
            _METHOD_SEND_STREAMING, request, context
        )

        if not broadcast_topic:
            broadcast_topic = self._topic

        # How many *final* responses we expect before stopping the stream.
        expected_finals = (
            message_limit if message_limit is not None else len(recipients)
        )
        wants_all = bool(recipients) and expected_finals >= len(recipients)
        tally = _BroadcastTally(expected_finals, recipients if wants_all else None)

        # Do NOT pass message_limit to the transport — let it stream
        # everything (intermediates + finals).  We manage the stop
        # condition here based on final response count.
        stream = self._transport.gather_stream(
            broadcast_topic,
            msg,
            recipients=recipients,
            timeout=timeout,
        )
        try:
            async for raw_resp in stream:
                # Only A2AStatusUpdate messages are intermediate; every other
                # message (including an error reply) is one recipient's final.
                is_final = raw_resp.type != "A2AStatusUpdate"
                logger.debug("Broadcast reply: %s", raw_resp)
                sender = _reply_sender(raw_resp)
                if is_final and not tally.accept_final(sender):
                    continue
                try:
                    event = await self._read_reply(
                        raw_resp,
                        StreamResponse(),
                        method=_METHOD_SEND_STREAMING,
                        context=context,
                        sender=sender,
                    )
                except Exception as e:
                    # A failed final still counts, otherwise we would wait
                    # out the full timeout for a reply that has already
                    # arrived.  It is reported once the stream has ended.
                    # (A failed intermediate was already logged; skip it.)
                    if is_final:
                        tally.failures.append(BroadcastFailure(sender, e))
                else:
                    if is_final:
                        tally.succeeded.append(sender)
                    yield event

                if is_final and tally.done:
                    break

            if tally.incomplete:
                raise tally.error(keep_responses=False)
        except A2ABroadcastError:
            # Must precede ``TimeoutError``: A2ABroadcastTimeoutError is one.
            raise
        except TimeoutError as e:
            raise tally.error(timed_out=True, keep_responses=False) from e
        finally:
            # ``break`` does not close an async generator; close it now so the
            # transport releases its sessions instead of at loop shutdown.
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

    async def start_groupchat(
        self,
        init_message: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> List[SendMessageResponse]:
        """Start a group chat conversation via transport."""
        msg, context = await self._build_message(_METHOD_SEND, init_message, context)
        try:
            member_messages = await self._transport.start_conversation(
                group_channel=group_channel,
                participants=participants,
                init_message=msg,
                end_message=end_message,
                timeout=timeout,
            )
            groupchat_messages = []
            for raw_msg in member_messages:
                try:
                    resp = json.loads(raw_msg.payload.decode("utf-8"))
                    smr = self._parse_payload(
                        resp, raw_msg.status_code, SendMessageResponse()
                    )
                    smr = await run_after_interceptors(
                        self._interceptors,
                        method=_METHOD_SEND,
                        result=smr,
                        agent_card=self._agent_card,
                        context=context,
                    )
                    groupchat_messages.append(smr)
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue

            return groupchat_messages
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error starting group chat A2A request with transport {self._transport.type()}: {e}"
            )
            return []

    async def start_streaming_groupchat(
        self,
        init_message: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> AsyncIterator[StreamResponse]:
        """Start a streaming group chat conversation via transport.

        Yields one ``StreamResponse`` per participant message (a superset of
        ``SendMessageResponse``: it can carry a task, a message, or a status
        / artifact update).
        """
        msg, context = await self._build_message(_METHOD_SEND, init_message, context)

        async for raw_member_message in self._transport.start_streaming_conversation(
            group_channel=group_channel,
            participants=participants,
            init_message=msg,
            end_message=end_message,
            timeout=timeout,
        ):
            message = json.loads(raw_member_message.payload.decode("utf-8"))
            event = self._parse_payload(
                message, raw_member_message.status_code, StreamResponse()
            )
            yield await run_after_interceptors(
                self._interceptors,
                method=_METHOD_SEND,
                result=event,
                agent_card=self._agent_card,
                context=context,
            )
