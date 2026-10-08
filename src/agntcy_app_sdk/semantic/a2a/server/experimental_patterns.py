# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Experimental A2A server for communication patterns beyond the A2A spec.

The A2A specification defines a point-to-point, request/response protocol
between agents. This module extends that model to explore additional
architectural patterns — publish/subscribe (broadcast) and multi-party
group communication — over non-HTTP transports such as SLIM and NATS.

Despite operating outside the A2A spec's transport assumptions, the
experimental server preserves the core benefits of the A2A ecosystem:
AgentCard-based discovery and handshake, JSON-RPC message envelopes,
and ProtoJSON-encoded A2A request payloads. Agents running behind this
server are still discoverable via their AgentCard and speak the same
wire format as standard A2A agents.

Internally, incoming transport messages are routed directly to the
a2a-sdk's ``RequestHandler`` — the layer that sits between the HTTP/ASGI
routing and the user's ``AgentExecutor``. This lets us bypass the full
Starlette/ASGI stack (unnecessary for non-HTTP transports) while still
getting task management, request validation and streaming support for free.
The JSON-RPC envelope handling mirrors what a2a-sdk's own JSON-RPC route
dispatcher does for HTTP.
"""

import json
import os
from collections.abc import AsyncIterable
from typing import Any, Optional
from uuid import uuid4

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    MethodNotFoundError,
)
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    HTTPAuthSecurityScheme,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    SendMessageResponse,
    StringList,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils import proto_utils
from a2a.utils.errors import A2AError
from google.protobuf.json_format import MessageToDict, ParseDict

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import add_interface, clone_card
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.semantic.a2a.server.config import A2AServerConfig
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport

from identityservice.sdk import IdentityServiceSdk

logger = get_logger(__name__)

# Maps BaseTransport.type() -> (interface protocol_binding, URI scheme)
_TRANSPORT_NAME_MAP: dict[str, tuple[str, str]] = {
    "SLIM": ("slimpatterns", "slim"),
    "NATS": ("natspatterns", "nats"),
}


def _default_topic(agent_card: AgentCard) -> str:
    """Derive a fallback topic from an agent card's name and version.

    Used when :func:`~agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier`
    returns ``None`` (no matching interface on the card).
    """
    return f"{agent_card.name}_{agent_card.version}".replace(" ", "_")


# JSON-RPC method name -> proto request type.  Method names follow the A2A
# v1.0 JSON-RPC binding (the gRPC service method names).
_A2A_METHOD_TO_MODEL: dict[str, type] = {
    "SendMessage": SendMessageRequest,
    "SendStreamingMessage": SendMessageRequest,
    "GetTask": GetTaskRequest,
    "ListTasks": ListTasksRequest,
    "CancelTask": CancelTaskRequest,
    "CreateTaskPushNotificationConfig": TaskPushNotificationConfig,
    "GetTaskPushNotificationConfig": GetTaskPushNotificationConfigRequest,
    "ListTaskPushNotificationConfigs": ListTaskPushNotificationConfigsRequest,
    "DeleteTaskPushNotificationConfig": DeleteTaskPushNotificationConfigRequest,
    "SubscribeToTask": SubscribeToTaskRequest,
    "GetExtendedAgentCard": GetExtendedAgentCardRequest,
}

# Streaming methods -> ``RequestHandler`` async-generator method name.
_STREAMING_METHODS: dict[str, str] = {
    "SendStreamingMessage": "on_message_send_stream",
    "SubscribeToTask": "on_subscribe_to_task",
}

# Unary methods -> ``RequestHandler`` coroutine method name.
_UNARY_METHODS: dict[str, str] = {
    "SendMessage": "on_message_send",
    "GetTask": "on_get_task",
    "ListTasks": "on_list_tasks",
    "CancelTask": "on_cancel_task",
    "CreateTaskPushNotificationConfig": "on_create_task_push_notification_config",
    "GetTaskPushNotificationConfig": "on_get_task_push_notification_config",
    "ListTaskPushNotificationConfigs": "on_list_task_push_notification_configs",
    "DeleteTaskPushNotificationConfig": "on_delete_task_push_notification_config",
    "GetExtendedAgentCard": "on_get_extended_agent_card",
}


class IdentityServiceUser(User):
    """Authenticated user validated by the Identity Service."""

    def __init__(self, user_name: str = "identity-service-user"):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


class A2AExperimentalServer:
    """Server-side bridge for A2A over experimental transports (SLIM/NATS).

    Calls the a2a-sdk ``RequestHandler`` directly with typed proto request
    objects and a manually-constructed ``ServerCallContext``, bypassing the
    full ASGI / Starlette stack that is unnecessary for non-HTTP transports.
    """

    def __init__(self) -> None:
        self._server: A2AServerConfig | None = None
        self._handler: Any | None = None
        self._auth_enabled: bool = False
        self._identity_sdk: IdentityServiceSdk | None = None

    def type(self):
        return "A2A"

    @staticmethod
    def create_transport_uri(
        agent_card: AgentCard,
        transport_type: str,
        *,
        topic: Optional[str] = None,
    ) -> str:
        """Build the transport URI for an agent card.

        Returns the scheme-prefixed topic that the server declares on the
        card's ``supported_interfaces`` at startup — e.g.
        ``"slim://Weather_Agent_1.0.0"``.

        This is useful when you need the URI but want to build the interface
        yourself.  For the common case, prefer :meth:`create_client_card`
        which returns a ready-to-use card copy.

        Args:
            agent_card: The base agent card (as defined by the agent author).
            transport_type: Transport type string (``"SLIM"`` or ``"NATS"``).
            topic: Optional pre-computed topic string.  When provided it is
                used directly instead of being derived from the card via
                :func:`~agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier`.

        Returns:
            A URI string like ``"slim://Weather_Agent_1.0.0"``.

        Raises:
            ValueError: If ``transport_type`` is not supported.
        """
        from agntcy_app_sdk.semantic.a2a.utils import get_agent_identifier

        entry = _TRANSPORT_NAME_MAP.get(transport_type)
        if entry is None:
            raise ValueError(
                f"Unsupported transport type {transport_type!r}. "
                f"Supported: {list(_TRANSPORT_NAME_MAP)}"
            )
        binding, scheme = entry
        if topic is None:
            # Scope the lookup to this transport's pub/sub binding.  Without
            # it the card's *first* interface is used whatever its transport,
            # so e.g. a leading ``slimrpc`` interface would leak its RPC
            # identity in as the pub/sub topic.
            topic = get_agent_identifier(agent_card, binding) or _default_topic(
                agent_card
            )
        return f"{scheme}://{topic}"

    @staticmethod
    def create_client_card(
        agent_card: AgentCard,
        transport_type: str,
        *,
        topic: Optional[str] = None,
    ) -> AgentCard:
        """Build a client-side AgentCard with transport metadata.

        Returns a copy of the card whose preferred (first) entry in
        ``supported_interfaces`` is the patterns transport, with a URL that
        matches what the server declares at startup.  The returned card is
        ready for ``factory.a2a(config).create(card)``.

        This is the client-side counterpart of the server's ``setup()``
        method which declares the same interface on its own copy of the card.

        Args:
            agent_card: The base agent card (as defined by the agent author).
            transport_type: Transport type string (``"SLIM"`` or ``"NATS"``).
            topic: Optional pre-computed topic string.  When provided it is
                used directly instead of being derived from the card via
                :func:`~agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier`.
                Useful when the server was started with an explicit topic
                that differs from the auto-derived format.

        Returns:
            A copy of the card with the patterns interface made preferred.

        Raises:
            ValueError: If ``transport_type`` is not supported.

        Example::

            from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
                A2AExperimentalServer,
            )

            # Topic auto-derived from the card's name and version:
            card = A2AExperimentalServer.create_client_card(agent_card, "SLIM")

            # Explicit topic (e.g. for broadcast or custom routing):
            card = A2AExperimentalServer.create_client_card(
                agent_card, "SLIM", topic="my_custom_topic",
            )

            client = await factory.a2a(config).create(card)
        """
        entry = _TRANSPORT_NAME_MAP.get(transport_type)
        if entry is None:
            raise ValueError(
                f"Unsupported transport type {transport_type!r}. "
                f"Supported: {list(_TRANSPORT_NAME_MAP)}"
            )
        binding, _scheme = entry
        uri = A2AExperimentalServer.create_transport_uri(
            agent_card, transport_type, topic=topic
        )
        card = clone_card(agent_card)
        add_interface(card, binding, uri, prefer=True, replace=True)
        return card

    def bind_server(self, server: A2AServerConfig) -> None:
        """Bind the protocol to a server config and extract its RequestHandler."""
        self._server = server
        self._handler = server.request_handler

    async def setup(self) -> None:
        """Configure auth and tracing. No ASGI app is created."""
        if not self._server:
            raise ValueError(
                "A2A server is not bound to the protocol, please bind it first"
            )
        if self._handler is None:
            raise ValueError(
                "RequestHandler is not available. Was bind_server() called?"
            )

        if is_identity_auth_enabled():
            logger.debug("Identity auth enabled — configuring direct auth guard")
            try:
                self._configure_identity_auth()
            except Exception as e:
                logger.warning(f"Failed to configure identity auth: {e}")

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()

    def _configure_identity_auth(self) -> None:
        """Configure identity authentication for the server."""
        assert self._server is not None  # Guarded by setup()

        # Stamp agent card security schemes (needed for client discovery)
        AUTH_SCHEME = "IdentityServiceAuthScheme"
        card = self._server.agent_card
        card.security_schemes[AUTH_SCHEME].CopyFrom(
            SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme="bearer",
                    bearer_format="JWT",
                )
            )
        )
        del card.security_requirements[:]
        card.security_requirements.append(
            SecurityRequirement(schemes={AUTH_SCHEME: StringList(list=["*"])})
        )

        # Direct SDK instead of ASGI middleware
        self._identity_sdk = IdentityServiceSdk()
        self._auth_enabled = True

    def _authenticate(self, message: Message) -> tuple[bool, str, User]:
        """Direct auth gate. Returns (success, error_reason, user)."""
        _unauthenticated = UnauthenticatedUser()

        if not self._auth_enabled:
            return True, "", _unauthenticated

        auth_header = message.headers.get("Authorization") or message.headers.get(
            "authorization"
        )
        if not auth_header or not auth_header.startswith("Bearer "):
            return False, "Missing or malformed Authorization header", _unauthenticated

        token = auth_header.split("Bearer ", 1)[1]
        if not token:
            return False, "Empty bearer token", _unauthenticated

        try:
            self._identity_sdk.authorize(access_token=token)  # type: ignore[union-attr]
            return True, "", IdentityServiceUser()
        except Exception as e:
            return False, f"Authentication failed: {e}", _unauthenticated

    # ------------------------------------------------------------------
    # JSON-RPC helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_error_payload(
        request_id: str | int | None,
        error: A2AError | JSONRPCError,
    ) -> bytes:
        """Serialize a JSON-RPC error response to bytes."""
        return json.dumps(build_error_response(request_id, error)).encode("utf-8")

    @staticmethod
    def _build_result_payload(request_id: str | int | None, result: Any) -> bytes:
        """Serialize a JSON-RPC success response to bytes."""
        return json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result}
        ).encode("utf-8")

    @staticmethod
    def _unwrap_relay(body: bytes) -> bytes:
        """Re-wrap a relayed JSON-RPC *response* as a ``SendMessage`` request.

        Relay scenario: an upstream agent forwards the JSON-RPC success
        response it received (whose result carries a ``Message``) as the
        input for the next agent.  If *body* is such a response, return an
        equivalent ``SendMessage`` request; otherwise return *body* as-is.
        """
        try:
            envelope = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return body

        if (
            not isinstance(envelope, dict)
            or "method" in envelope
            or "result" not in envelope
        ):
            return body

        result = envelope["result"]
        if not isinstance(result, dict):
            return body

        # v1 SendMessageResponse wraps the message under "message"; a bare
        # message dict is also accepted.
        if isinstance(result.get("message"), dict):
            msg = result["message"]
        elif "messageId" in result and "parts" in result:
            msg = result
        else:
            return body

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid4()),
            "method": "SendMessage",
            "params": {"message": msg},
        }
        return json.dumps(request).encode("utf-8")

    @staticmethod
    def _serialize_unary(method: str, result: Any) -> Any:
        """Convert a unary ``RequestHandler`` result to a JSON-able value."""
        if method == "SendMessage":
            if isinstance(result, Task):
                return MessageToDict(SendMessageResponse(task=result))
            return MessageToDict(SendMessageResponse(message=result))
        if method == "DeleteTaskPushNotificationConfig":
            return None
        if method == "ListTasks":
            return MessageToDict(
                result,
                preserving_proto_field_name=False,
                always_print_fields_with_no_presence=True,
            )
        return MessageToDict(result, preserving_proto_field_name=False)

    @staticmethod
    def _serialize_stream_item(item: Any) -> dict[str, Any]:
        """Convert a streamed event to a ``StreamResponse`` JSON dict."""
        return MessageToDict(
            proto_utils.to_stream_response(item), preserving_proto_field_name=False
        )

    async def handle_message(self, message: Message, *, publish_fn=None) -> Message:
        """Handle an incoming request by calling the RequestHandler directly.

        Args:
            message: The incoming transport-level message.
            publish_fn: Optional async callable to publish intermediate
                messages (e.g. ``A2AStatusUpdate``) back to the client
                *before* returning the final response.  When ``None``,
                streaming handlers are drained to their last item
                (backward-compatible behaviour).
        """
        assert self._handler is not None, "RequestHandler is not set up"

        logger.debug(f"Handling A2A message with payload: {message}")

        request_id: str | int | None = None

        def _reply(payload: bytes) -> Message:
            return Message(
                type="A2AResponse",
                payload=payload,
                reply_to=message.reply_to,
            )

        try:
            # ---- Auth guard ------------------------------------------------
            auth_ok, auth_reason, user = self._authenticate(message)
            if not auth_ok:
                return _reply(
                    self._build_error_payload(None, InternalError(data=auth_reason))
                )

            # ---- Relay preservation ----------------------------------------
            body = self._unwrap_relay(message.payload)

            # ---- Parse JSON ------------------------------------------------
            try:
                raw: Any = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return _reply(self._build_error_payload(None, JSONParseError()))

            # ---- Validate the JSON-RPC envelope ----------------------------
            if not isinstance(raw, dict):
                return _reply(
                    self._build_error_payload(
                        None,
                        InvalidRequestError(data="Request payload validation error"),
                    )
                )

            raw_id = raw.get("id")
            request_id = raw_id if isinstance(raw_id, (str, int)) else None
            method = raw.get("method")

            if raw.get("jsonrpc") != "2.0" or not isinstance(method, str):
                return _reply(
                    self._build_error_payload(
                        request_id,
                        InvalidRequestError(data="Request payload validation error"),
                    )
                )

            # ---- Route by method -------------------------------------------
            model_class = _A2A_METHOD_TO_MODEL.get(method)
            if model_class is None:
                return _reply(
                    self._build_error_payload(request_id, MethodNotFoundError())
                )

            # ---- Parse params into the typed proto request -----------------
            try:
                typed_request = ParseDict(raw.get("params", {}), model_class())
            except Exception as e:
                return _reply(
                    self._build_error_payload(
                        request_id, InvalidParamsError(data=str(e))
                    )
                )

            # ---- Build ServerCallContext -----------------------------------
            context = ServerCallContext(
                user=user,
                state={
                    "headers": dict(message.headers),
                    "method": method,
                    "request_id": request_id,
                },
                tenant=getattr(typed_request, "tenant", ""),
            )

            # ---- Dispatch --------------------------------------------------
            stream_attr = _STREAMING_METHODS.get(method)
            if stream_attr is not None:
                return await self._handle_streaming(
                    stream_attr,
                    typed_request,
                    context,
                    request_id,
                    message,
                    publish_fn,
                )

            unary_attr = _UNARY_METHODS.get(method)
            if unary_attr is None:
                return _reply(
                    self._build_error_payload(request_id, MethodNotFoundError())
                )

            try:
                result = await getattr(self._handler, unary_attr)(
                    typed_request, context
                )
            except A2AError as e:
                return _reply(self._build_error_payload(request_id, e))

            return _reply(
                self._build_result_payload(
                    request_id, self._serialize_unary(method, result)
                )
            )

        except Exception as e:
            logger.exception(f"Error handling A2A message: {e}")
            return _reply(
                self._build_error_payload(request_id, InternalError(data=str(e)))
            )

    async def _handle_streaming(
        self,
        handler_attr: str,
        typed_request: Any,
        context: ServerCallContext,
        request_id: str | int | None,
        message: Message,
        publish_fn,
    ) -> Message:
        """Drain a streaming handler, publishing intermediate items.

        When a ``publish_fn`` is provided (SLIM streaming), each
        intermediate item is published as an ``A2AStatusUpdate`` message back
        to the client; only the final item is returned as the
        ``A2AResponse``.  When ``publish_fn`` is ``None`` (backward compat /
        NATS), the generator is drained to its last item and returned
        directly.
        """

        def _envelope(item: Any) -> bytes:
            return self._build_result_payload(
                request_id, self._serialize_stream_item(item)
            )

        def _reply(payload: bytes) -> Message:
            return Message(
                type="A2AResponse", payload=payload, reply_to=message.reply_to
            )

        stream = getattr(self._handler, handler_attr)(typed_request, context)
        if not isinstance(stream, AsyncIterable):  # pragma: no cover - defensive
            stream = await stream

        last_item = None
        try:
            async for item in stream:
                if publish_fn is not None and last_item is not None:
                    # Publish the *previous* item as an intermediate
                    # status update before replacing it.
                    await publish_fn(
                        Message(
                            type="A2AStatusUpdate",
                            payload=_envelope(last_item),
                            reply_to=message.reply_to,
                        )
                    )
                last_item = item
        except A2AError as e:
            return _reply(self._build_error_payload(request_id, e))

        if last_item is None:
            return _reply(
                self._build_error_payload(
                    request_id,
                    InternalError(data="Streaming handler returned no items"),
                )
            )

        return _reply(_envelope(last_item))


class A2AExperimentalServerHandler(BaseA2AServerHandler):
    """A2A handler that bridges an :class:`A2AServerConfig` over a
    ``BaseTransport`` (SLIM or NATS pub-sub patterns).

    Declares a ``slimpatterns`` or ``natspatterns`` interface on the agent
    card's ``supported_interfaces`` depending on the transport type.
    """

    def __init__(
        self,
        server: A2AServerConfig,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
    ):
        # Auto-derive topic from agent_card if not provided
        if topic is None or topic == "":
            from agntcy_app_sdk.semantic.a2a.utils import get_agent_identifier

            # Scope the lookup to this handler's pub/sub binding so a card
            # whose first interface is another transport (e.g. ``slimrpc``,
            # which also uses ``slim://`` URLs) cannot supply the topic.
            # Without a transport the binding is unknown; fall back to the
            # card's preferred interface.
            entry = (
                _TRANSPORT_NAME_MAP.get(transport.type())
                if transport is not None
                else None
            )
            binding = entry[0] if entry else None
            topic = get_agent_identifier(server.agent_card, binding) or _default_topic(
                server.agent_card
            )

        super().__init__(server, transport=transport, topic=topic)
        self._protocol = A2AExperimentalServer()

    # -- agent_card property (required by BaseA2AServerHandler) -----------

    @property
    def agent_card(self) -> AgentCard:
        return self._managed_object.agent_card

    # -- Lifecycle --------------------------------------------------------

    async def setup(self) -> None:
        """Full lifecycle: transport.setup() -> set_callback() -> subscribe() -> directory -> protocol.setup()."""
        if self._transport is None:
            raise ValueError("Transport must be set before running A2A handler.")

        # Declare the patterns interface on the card before anything else.
        # When ``CardBuilder`` registers the same server config with multiple
        # handlers (SLIM + NATS + HTTP), each handler shares the same card
        # object.  An interface the agent author already declared is left
        # untouched (preserving its URL and position); only a missing one is
        # added, and it becomes the preferred transport if it is added.
        transport_type = self._transport.type()
        transport_entry = _TRANSPORT_NAME_MAP.get(transport_type)
        if transport_entry:
            binding, scheme = transport_entry
            self._declare_interface(binding, f"{scheme}://{self._topic}", prefer=True)
        else:
            logger.warning(
                f"Unknown transport type '{transport_type}'; "
                "no interface declared on the agent card."
            )

        # Transport setup
        await self._transport.setup()

        # Bind server and create the protocol bridge
        self._protocol.bind_server(self._managed_object)

        # Set callback for incoming messages
        self._transport.set_callback(self._protocol.handle_message)

        # Subscribe to topic
        await self._transport.subscribe(self._topic)

        # Protocol-level setup (auth, tracing, etc.)
        await self._protocol.setup()

        logger.debug(f"A2A experimental handler started on topic: {self._topic}")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.debug("A2A transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing A2A transport: {e}")
