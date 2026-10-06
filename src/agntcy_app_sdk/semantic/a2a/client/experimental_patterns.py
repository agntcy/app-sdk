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
from typing import Any, Callable, List
from uuid import uuid4

from a2a.client.client import Client, ClientCallContext
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
    def _parse_payload(
        payload: dict[str, Any],
        status_code: int | None,
        proto: ProtoMessage,
    ) -> ProtoMessage:
        """Parse a raw transport payload (JSON-RPC response) into *proto*.

        Identity-auth rejections become a synthetic agent message; JSON-RPC
        errors raise the matching ``A2AError``.
        """
        if payload.get("error") == "forbidden" or status_code == 403:
            logger.warning("Received forbidden error in A2A response: %s", payload)
            payload = get_identity_auth_error()
        elif "error" in payload:
            raise create_rpc_error(payload["error"])
        result = payload.get("result", payload)
        return ParseDict(result, proto, ignore_unknown_fields=True)

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
        context: ClientCallContext | None = None,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        timeout: float = 60.0,
    ) -> List[SendMessageResponse]:
        """Broadcast a message to multiple recipients via transport.

        When used with streaming agents the transport may deliver
        intermediate ``A2AStatusUpdate`` messages before the final
        ``A2AResponse``.  This method filters those out and returns
        only the final responses (one per recipient).
        """
        msg, context = await self._build_message(_METHOD_SEND, request, context)

        if not broadcast_topic:
            broadcast_topic = self._topic

        expected = len(recipients) if recipients else 1

        stream = self._transport.gather_stream(
            broadcast_topic,
            msg,
            recipients=recipients,
            timeout=timeout,
        )
        try:
            broadcast_responses: List[SendMessageResponse] = []
            async for raw_resp in stream:
                try:
                    # Only collect final A2AResponse messages; skip
                    # intermediate A2AStatusUpdate messages that streaming
                    # agents emit.
                    if raw_resp.type == "A2AStatusUpdate":
                        continue

                    resp = json.loads(raw_resp.payload.decode("utf-8"))
                    smr = self._parse_payload(
                        resp, raw_resp.status_code, SendMessageResponse()
                    )
                    smr = await run_after_interceptors(
                        self._interceptors,
                        method=_METHOD_SEND,
                        result=smr,
                        agent_card=self._agent_card,
                        context=context,
                    )
                    broadcast_responses.append(smr)

                    if len(broadcast_responses) >= expected:
                        break
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue

            return broadcast_responses
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error gathering A2A request with transport {self._transport.type()}: {e}"
            )
            return []
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
        context: ClientCallContext | None = None,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        message_limit: int | None = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[StreamResponse]:
        """Broadcast with streaming responses, including intermediate status events.

        Yields a ``StreamResponse`` for every event as it arrives from each
        recipient: intermediate ``status_update`` / ``task`` events, plus one
        final response per recipient.  The stream ends after *message_limit*
        final responses have been received (defaults to ``len(recipients)``).
        """
        msg, context = await self._build_message(
            _METHOD_SEND_STREAMING, request, context
        )

        if not broadcast_topic:
            broadcast_topic = self._topic

        # How many *final* responses we expect before stopping the stream.
        expected_finals = (
            message_limit
            if message_limit is not None
            else (len(recipients) if recipients else 1)
        )

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
            finals_received = 0
            async for raw_resp in stream:
                try:
                    logger.debug(raw_resp)
                    resp = json.loads(raw_resp.payload.decode("utf-8"))

                    event = self._parse_payload(
                        resp, raw_resp.status_code, StreamResponse()
                    )
                    event = await run_after_interceptors(
                        self._interceptors,
                        method=_METHOD_SEND_STREAMING,
                        result=event,
                        agent_card=self._agent_card,
                        context=context,
                    )
                    yield event

                    # Intermediates don't count toward finals.
                    if raw_resp.type == "A2AStatusUpdate":
                        continue

                    finals_received += 1
                    if finals_received >= expected_finals:
                        break
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error gathering streaming A2A request with transport {self._transport.type()}: {e}"
            )
            return
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
