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
and typed ``MessageSendParams`` payloads. Agents running behind this
server are still discoverable via their AgentCard and speak the same
wire format as standard A2A agents.

Internally, incoming transport messages are routed directly to the
a2a-sdk's ``JSONRPCHandler`` — the framework layer that sits between
ASGI routing and the user-provided ``RequestHandler``. This lets us
bypass the full Starlette/ASGI stack (unnecessary for non-HTTP
transports) while still getting JSON-RPC envelope handling, request
validation, error translation, and streaming response support for free.
The method-to-handler dispatch tables are derived from the SDK at import
time via introspection, so they stay in sync as the SDK evolves.
"""

import inspect
import json
import os
from collections.abc import AsyncIterable
from typing import Any, Optional, Union
from uuid import uuid4

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.apps import A2AStarletteApplication
from a2a.server.apps.jsonrpc.jsonrpc_app import JSONRPCApplication
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.jsonrpc_handler import JSONRPCHandler
from a2a.types import (
    AgentCard,
    HTTPAuthSecurityScheme,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCSuccessResponse,
    MessageSendParams,
    MethodNotFoundError,
    SecurityScheme,
    SendMessageRequest,
)
from pydantic import ValidationError

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport

from identityservice.sdk import IdentityServiceSdk

configure_logging()
logger = get_logger(__name__)

# Maps BaseTransport.type() -> (preferred_transport name, URI scheme)
_TRANSPORT_NAME_MAP: dict[str, tuple[str, str]] = {
    "SLIM": ("slimpatterns", "slim"),
    "NATS": ("natspatterns", "nats"),
}

# Method name -> typed request model — derived from the SDK's canonical mapping.
_A2A_METHOD_TO_MODEL: dict[str, type] = dict(JSONRPCApplication.METHOD_TO_MODEL)

# Method name -> JSONRPCHandler method name — derived by matching each request
# model type against the first parameter annotation of each handler method.
_HANDLER_METHODS = {
    name: func
    for name, func in inspect.getmembers(JSONRPCHandler, predicate=inspect.isfunction)
    if not name.startswith("_")
}
_METHOD_TO_HANDLER: dict[str, str] = {}
for _method_name, _model in _A2A_METHOD_TO_MODEL.items():
    for _hname, _hfunc in _HANDLER_METHODS.items():
        _params = list(inspect.signature(_hfunc).parameters.values())
        if len(_params) >= 2 and _params[1].annotation is _model:
            _METHOD_TO_HANDLER[_method_name] = _hname
            break


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

    Calls the ``JSONRPCHandler`` directly with typed request objects and a
    manually-constructed ``ServerCallContext``, bypassing the full ASGI /
    Starlette stack that is unnecessary for non-HTTP transports.
    """

    def __init__(self) -> None:
        self._server: A2AStarletteApplication | None = None
        self._handler: JSONRPCHandler | None = None
        self._auth_enabled: bool = False
        self._identity_sdk: IdentityServiceSdk | None = None

    def type(self):
        return "A2A"

    @staticmethod
    def create_agent_topic(agent_card: AgentCard) -> str:
        """
        A standard way to create a topic for the agent card metadata.
        Spaces are replaced with underscores to ensure transport compatibility.
        """
        return f"{agent_card.name}_{agent_card.version}".replace(" ", "_")

    def bind_server(self, server: A2AStarletteApplication) -> None:
        """Bind the protocol to a server and extract the JSONRPCHandler."""
        self._server = server
        self._handler = server.handler  # Available at construction time

    async def setup(self) -> None:
        """Configure auth and tracing. No ASGI app is created."""
        if not self._server:
            raise ValueError(
                "A2A server is not bound to the protocol, please bind it first"
            )
        if self._handler is None:
            raise ValueError(
                "JSONRPCHandler is not available. Was bind_server() called?"
            )

        if is_identity_auth_enabled():
            logger.info("Identity auth enabled — configuring direct auth guard")
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
        auth_scheme = HTTPAuthSecurityScheme(
            scheme="bearer",
            bearerFormat="JWT",
        )
        self._server.agent_card.security_schemes = {
            AUTH_SCHEME: SecurityScheme(root=auth_scheme)
        }
        self._server.agent_card.security = [{AUTH_SCHEME: ["*"]}]

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

    @staticmethod
    def _build_error_payload(
        request_id: str | int | None,
        error: Union[
            JSONRPCError,
            JSONParseError,
            InvalidRequestError,
            MethodNotFoundError,
            InvalidParamsError,
            InternalError,
        ],
    ) -> bytes:
        """Serialize a JSON-RPC error response to bytes."""
        resp = JSONRPCErrorResponse(id=request_id, error=error)
        return json.dumps(resp.model_dump(mode="json", exclude_none=True)).encode(
            "utf-8"
        )

    async def handle_message(self, message: Message) -> Message:
        """Handle an incoming request by calling JSONRPCHandler directly."""
        assert self._handler is not None, "JSONRPCHandler is not set up"

        logger.debug(f"Handling A2A message with payload: {message}")

        request_id: str | int | None = None

        try:
            # ---- Auth guard ------------------------------------------------
            auth_ok, auth_reason, user = self._authenticate(message)
            if not auth_ok:
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(
                        None,
                        InternalError(data=auth_reason),
                    ),
                    reply_to=message.reply_to,
                )

            body = message.payload

            # ---- Relay preservation ----------------------------------------
            # If the body is a JSONRPCSuccessResponse (relay scenario),
            # re-wrap it as a SendMessageRequest.
            try:
                inner = JSONRPCSuccessResponse.model_validate_json(body)
                msg_params = {"message": inner.result}
                request = SendMessageRequest(
                    id=str(uuid4()),
                    params=MessageSendParams(**msg_params),
                )
                body = json.dumps(
                    request.model_dump(mode="json", exclude_none=True)
                ).encode("utf-8")
            except Exception:
                pass

            # ---- Parse JSON ------------------------------------------------
            try:
                raw: dict[str, Any] = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(None, JSONParseError()),
                    reply_to=message.reply_to,
                )

            # ---- Validate as generic JSONRPCRequest ------------------------
            try:
                base_request = JSONRPCRequest.model_validate(raw)
                request_id = base_request.id
            except ValidationError:
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(
                        raw.get("id"),
                        InvalidRequestError(data="Request payload validation error"),
                    ),
                    reply_to=message.reply_to,
                )

            method = base_request.method
            request_id = base_request.id

            # ---- Route by method -------------------------------------------
            model_class = _A2A_METHOD_TO_MODEL.get(method)
            if model_class is None:
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(
                        request_id, MethodNotFoundError()
                    ),
                    reply_to=message.reply_to,
                )

            # ---- Validate typed request model ------------------------------
            try:
                typed_request = model_class.model_validate(raw)
            except ValidationError as e:
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(
                        request_id,
                        InvalidParamsError(data=str(e)),
                    ),
                    reply_to=message.reply_to,
                )

            # ---- Build ServerCallContext -----------------------------------
            context = ServerCallContext(
                user=user,
                state={
                    "headers": dict(message.headers),
                    "method": method,
                },
            )

            # ---- Dispatch to handler ---------------------------------------
            handler_method_name = _METHOD_TO_HANDLER.get(method)
            if handler_method_name is None:
                return Message(
                    type="A2AResponse",
                    payload=self._build_error_payload(
                        request_id, MethodNotFoundError()
                    ),
                    reply_to=message.reply_to,
                )

            handler_method = getattr(self._handler, handler_method_name)
            handler_result = await handler_method(typed_request, context=context)

            # ---- Handle streaming responses --------------------------------
            # message/stream and tasks/resubscribe return AsyncIterable;
            # drain the generator and return the final item since
            # transport doesn't support streaming.
            if isinstance(handler_result, AsyncIterable):
                last_item = None
                async for item in handler_result:
                    last_item = item
                if last_item is None:
                    return Message(
                        type="A2AResponse",
                        payload=self._build_error_payload(
                            request_id,
                            InternalError(data="Streaming handler returned no items"),
                        ),
                        reply_to=message.reply_to,
                    )
                handler_result = last_item

            # ---- Serialize response ----------------------------------------
            payload = json.dumps(
                handler_result.root.model_dump(mode="json", exclude_none=True)
            ).encode("utf-8")

            return Message(
                type="A2AResponse",
                payload=payload,
                reply_to=message.reply_to,
            )

        except Exception as e:
            logger.exception(f"Error handling A2A message: {e}")
            return Message(
                type="A2AResponse",
                payload=self._build_error_payload(
                    request_id,
                    InternalError(data=str(e)),
                ),
                reply_to=message.reply_to,
            )


class A2AExperimentalServerHandler(BaseA2AServerHandler):
    """A2A handler that bridges an ``A2AStarletteApplication`` over a
    ``BaseTransport`` (SLIM or NATS pub-sub patterns).

    Sets ``preferred_transport`` to ``"slimpatterns"`` or
    ``"natspatterns"`` depending on the transport type.
    """

    def __init__(
        self,
        server: A2AStarletteApplication,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
    ):
        # Auto-derive topic from agent_card if not provided
        if topic is None or topic == "":
            topic = A2AExperimentalServer.create_agent_topic(server.agent_card)

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

        # Stamp preferred_transport and card.url before anything else
        transport_type = self._transport.type()
        transport_entry = _TRANSPORT_NAME_MAP.get(transport_type)
        if transport_entry:
            transport_name, scheme = transport_entry
            self._set_preferred_transport(transport_name)
            # Encode the topic into card.url so clients can derive it
            old_url = self._managed_object.agent_card.url
            new_url = f"{scheme}://{self._topic}"
            logger.info(
                "Overwriting card.url '%s' -> '%s' for %s transport",
                old_url,
                new_url,
                transport_name,
            )
            self._managed_object.agent_card.url = new_url
        else:
            logger.warning(
                f"Unknown transport type '{transport_type}'; "
                "preferred_transport not set."
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

        logger.info(f"A2A experimental handler started on topic: {self._topic}")

    async def teardown(self) -> None:
        """Close transport and clean up."""
        if self._transport:
            try:
                await self._transport.close()
                logger.info("A2A transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing A2A transport: {e}")
