# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, TypeVar
from urllib.parse import urlparse
from uuid import uuid4

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.transports.base import ClientTransport
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
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.message import Message as ProtoMessage

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.client.utils import (
    create_rpc_error,
    get_identity_auth_error,
    message_translator,
)
from agntcy_app_sdk.transport.base import BaseTransport

if TYPE_CHECKING:
    from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

logger = get_logger(__name__)

# Recognized URI schemes for patterns transports
_PATTERNS_SCHEMES = {"slim", "nats"}


def _parse_topic_from_url(url: str) -> str:
    """Extract a topic from a scheme-encoded URL.

    Handles both topic-only and explicit-endpoint formats::

        "slim://my_topic"                 →  "my_topic"
        "slim://localhost:46357/my_topic" →  "my_topic"
        "nats://my_topic"                 →  "my_topic"
        "nats://localhost:4222/my_topic"  →  "my_topic"
        "slim://default/default/agent"    →  "default/default/agent"
        "http://localhost:9999"           →  "http://localhost:9999"
    """
    if "://" not in url:
        return url
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _PATTERNS_SCHEMES:
        return url  # HTTP etc. — pass through unchanged
    # Explicit endpoint: has a port → topic is the path
    if parsed.port is not None:
        return parsed.path.lstrip("/")
    # Topic-only: hostname (+ path if slashes present) IS the topic
    hostname = parsed.hostname or ""
    path = parsed.path.lstrip("/")
    return f"{hostname}/{path}" if path else hostname


def _to_params(request: ProtoMessage) -> dict[str, Any]:
    """Serialize a proto request to its ProtoJSON ``params`` dict."""
    return MessageToDict(request, preserving_proto_field_name=False)


_P = TypeVar("_P", bound=ProtoMessage)


def _parse(result: Any, proto: _P) -> _P:
    """Parse a JSON-RPC ``result`` into *proto* (unknown fields tolerated)."""
    ParseDict(result or {}, proto, ignore_unknown_fields=True)
    return proto


class PatternsClientTransport(ClientTransport):
    """Adapts a ``BaseTransport`` (SLIM-patterns / NATS-patterns) to the
    upstream ``a2a.client.transports.base.ClientTransport`` interface.

    This lets the upstream ``ClientFactory`` treat SLIM/NATS transports the
    same way it treats ``JsonRpcTransport`` or ``GrpcTransport``.

    Standard A2A operations (``send_message``, ``get_task``, …) are routed
    through the transport's ``request()`` method using the internal
    ``Message`` wire format: a JSON-RPC 2.0 envelope whose ``params`` /
    ``result`` are ProtoJSON-encoded A2A v1 messages.  Streaming falls back
    to ``send_message`` when the transport cannot stream.

    Interceptors are applied by the upstream ``BaseClient`` that wraps this
    transport; anything they put in ``context.service_parameters`` is
    forwarded as transport message headers.
    """

    def __init__(
        self,
        transport: BaseTransport,
        agent_card: AgentCard,
        topic: str,
    ) -> None:
        self._transport = transport
        self._agent_card = agent_card
        self._topic = topic

    # ------------------------------------------------------------------
    # Factory method — matches ``TransportProducer`` signature
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        card: AgentCard,
        url: str,
        config: ClientConfig,
    ) -> PatternsClientTransport:
        """``TransportProducer`` compatible factory for upstream
        ``ClientFactory.register()``.

        This method is invoked **synchronously** by the upstream
        ``ClientFactory.create()`` call.  It can only use pre-built
        (eager) transports from the config.  For deferred transport
        construction (which requires ``await``), use
        ``A2AClientFactory.create()`` instead.

        The ``url`` parameter comes from the selected entry of
        ``card.supported_interfaces`` and is expected to be a
        scheme-encoded topic, e.g. ``slim://my_topic`` or
        ``nats://my_topic``.
        """
        topic = _parse_topic_from_url(url)
        transport_label = url
        for iface in card.supported_interfaces:
            if iface.url == url and iface.protocol_binding:
                transport_label = iface.protocol_binding
                break

        base_transport: BaseTransport | None = None
        if "slim" in str(transport_label).lower():
            base_transport = config.slim_transport
        elif "nats" in str(transport_label).lower():
            base_transport = config.nats_transport

        if base_transport is None:
            raise ValueError(
                f"No pre-built transport for '{transport_label}' on ClientConfig. "
                f"Set slim_transport or nats_transport for sync usage, "
                f"or use A2AClientFactory.create() for deferred construction."
            )

        return cls(base_transport, card, topic)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_headers(context: ClientCallContext | None) -> dict[str, str]:
        """Build transport message headers for a call.

        Starts from ``context.service_parameters`` (where interceptors put
        auth tokens and extension lists), adds the A2A protocol version,
        and — when identity auth is enabled — a bearer token unless an
        ``Authorization`` header is already present.
        """
        headers: dict[str, str] = {}
        if context is not None and context.service_parameters:
            headers.update(context.service_parameters)
        headers.setdefault(VERSION_HEADER, PROTOCOL_VERSION_CURRENT)

        if is_identity_auth_enabled() and not any(
            k.lower() == "authorization" for k in headers
        ):
            try:
                from identityservice.sdk import IdentityServiceSdk

                access_token = IdentityServiceSdk().access_token()
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
            except Exception as e:
                logger.error("Failed to get access token for agent: %s", e)
        return headers

    @staticmethod
    def _rpc_payload(method: str, request: ProtoMessage) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": method,
            "params": _to_params(request),
        }

    async def _send_rpc(
        self,
        method: str,
        request: ProtoMessage,
        context: ClientCallContext | None,
        *,
        forbidden_as_message: bool = False,
    ) -> Any:
        """Send one JSON-RPC call and return its ``result``.

        Raises the matching ``A2AError`` for JSON-RPC error responses.  An
        identity-auth rejection (HTTP 403 / ``"forbidden"``) is returned as a
        synthetic agent message when *forbidden_as_message* is set (message
        sends), and raised as ``A2AClientError`` otherwise.
        """
        try:
            response = await self._transport.request(
                self._topic,
                message_translator(
                    request=self._rpc_payload(method, request),
                    headers=self._build_headers(context),
                ),
            )
            payload = json.loads(response.payload.decode("utf-8"))
        except Exception as e:
            logger.error(
                "Error sending A2A request with transport %s: %s",
                self._transport.type(),
                e,
            )
            raise

        # Handle Identity-Middleware auth errors
        if payload.get("error") == "forbidden" or response.status_code == 403:
            logger.error(
                "Received forbidden error in A2A response due to identity auth"
            )
            if forbidden_as_message:
                return get_identity_auth_error()["result"]
            raise A2AClientError("Access forbidden: identity auth failed")

        if "error" in payload:
            raise create_rpc_error(payload["error"])

        return payload.get("result", payload)

    # ------------------------------------------------------------------
    # ClientTransport interface
    # ------------------------------------------------------------------

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Send a non-streaming message and return the response."""
        result = await self._send_rpc(
            "SendMessage", request, context, forbidden_as_message=True
        )
        return _parse(result, SendMessageResponse())

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Stream A2A events from the server over the patterns transport.

        Uses ``request_stream()`` on the underlying transport, which keeps
        a SLIM point-to-point session open across multiple messages.
        Each intermediate ``A2AStatusUpdate`` message and the final
        ``A2AResponse`` are parsed and yielded as ``StreamResponse``.

        Falls back to a single ``send_message()`` if the transport
        does not implement ``request_stream()``.
        """
        transport_msg = message_translator(
            request=self._rpc_payload("SendStreamingMessage", request),
            headers=self._build_headers(context),
        )

        stream = self._transport.request_stream(self._topic, transport_msg)
        try:
            async for response in stream:
                response_payload = json.loads(response.payload.decode("utf-8"))

                # Handle JSON-RPC error responses
                if "error" in response_payload:
                    error_data = response_payload.get("error", {})
                    logger.error(
                        "Server returned JSON-RPC error in streaming response: %s",
                        error_data,
                    )
                    raise create_rpc_error(error_data)

                result = response_payload.get("result", response_payload)
                yield _parse(result, StreamResponse())

                # The transport Message.type distinguishes intermediate
                # ("A2AStatusUpdate") from final ("A2AResponse") messages.
                # Stop reading once the final response has been yielded,
                # mirroring how SSE/gRPC transports end when the server
                # closes the stream.
                if response.type == "A2AResponse":
                    break
        except NotImplementedError:
            # Transport doesn't support streaming — fall back to single
            # request/reply.
            single = await self.send_message(request, context=context)
            stream_response = StreamResponse()
            if single.HasField("task"):
                stream_response.task.CopyFrom(single.task)
            elif single.HasField("message"):
                stream_response.message.CopyFrom(single.message)
            yield stream_response
        finally:
            # ``break`` does not close an async generator.  Close it here so
            # the transport releases its session (SLIM: closes the session)
            # now, not at event-loop shutdown where it can hang the process.
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieve a task by ID."""
        result = await self._send_rpc("GetTask", request, context)
        return _parse(result, Task())

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """List tasks known to the agent."""
        result = await self._send_rpc("ListTasks", request, context)
        return _parse(result, ListTasksResponse())

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Cancel a task by ID."""
        result = await self._send_rpc("CancelTask", request, context)
        return _parse(result, Task())

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Create/update the push notification config for a task."""
        result = await self._send_rpc(
            "CreateTaskPushNotificationConfig", request, context
        )
        return _parse(result, TaskPushNotificationConfig())

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Get the push notification config for a task."""
        result = await self._send_rpc("GetTaskPushNotificationConfig", request, context)
        return _parse(result, TaskPushNotificationConfig())

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """List push notification configs for a task."""
        result = await self._send_rpc(
            "ListTaskPushNotificationConfigs", request, context
        )
        return _parse(result, ListTaskPushNotificationConfigsResponse())

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Delete a push notification config."""
        await self._send_rpc("DeleteTaskPushNotificationConfig", request, context)

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Subscribe to task updates — not supported by patterns transports."""
        raise NotImplementedError("subscribe is not supported by patterns transports")
        # Make the method a valid async generator
        yield  # pragma: no cover

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Return the agent card.

        Asks the server only when the card advertises an extended card;
        otherwise returns the locally-cached card.
        """
        if not self._agent_card.capabilities.extended_agent_card:
            return self._agent_card
        result = await self._send_rpc("GetExtendedAgentCard", request, context)
        return _parse(result, AgentCard())

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._transport.close()
