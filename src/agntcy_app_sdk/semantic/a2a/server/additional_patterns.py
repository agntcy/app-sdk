# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
import os
from typing import Any, Dict, Optional
from uuid import uuid4

from a2a.server.apps import A2AStarletteApplication
from a2a.types import (
    AgentCard,
    HTTPAuthSecurityScheme,
    JSONRPCSuccessResponse,
    MessageSendParams,
    SecurityScheme,
    SendMessageRequest,
)
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from starlette.types import Scope

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport

from identityservice.auth.starlette import IdentityServiceA2AMiddleware

configure_logging()
logger = get_logger(__name__)

# Maps BaseTransport.type() → (preferred_transport name, URI scheme)
_TRANSPORT_NAME_MAP: dict[str, tuple[str, str]] = {
    "SLIM": ("slimpatterns", "slim"),
    "NATS": ("natspatterns", "nats"),
}


class A2AExperimentalServer:
    """Server-side ASGI bridge for A2A over experimental transports (SLIM/NATS).

    Translates between the internal ``Message`` wire format and ASGI HTTP
    scopes so that an ``A2AStarletteApplication`` can serve requests
    arriving over non-HTTP transports.
    """

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
        """Bind the protocol to a server."""
        self._server = server

    async def setup(self) -> None:
        """
        Create a bridge between the A2A server/ASGI app and our internal message type.
        """

        if not self._server:
            raise ValueError(
                "A2A server is not bound to the protocol, please bind it first"
            )

        if is_identity_auth_enabled():
            logger.info("Identity auth enabled")
            try:
                self._configure_identity_auth()
            except Exception as e:
                logger.warning(f"Failed to add IdentityServiceMCPMiddleware: {e}")
        else:
            self._app = self._server.build()

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()

    def _configure_identity_auth(self) -> None:
        """Configure identity authentication for the server."""
        AUTH_SCHEME = "IdentityServiceAuthScheme"
        auth_scheme = HTTPAuthSecurityScheme(
            scheme="bearer",
            bearerFormat="JWT",
        )
        self._server.agent_card.security_schemes = {
            AUTH_SCHEME: SecurityScheme(root=auth_scheme)
        }
        self._server.agent_card.security = [{AUTH_SCHEME: ["*"]}]

        self._app = self._server.build()
        self._app.add_middleware(
            IdentityServiceA2AMiddleware,  # Define the middleware
            agent_card=self._server.agent_card,
            public_paths=[AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH],
        )

    async def handle_message(self, message: Message) -> Message:
        """
        Handle an incoming request and return a response.
        """
        assert self._app is not None, "ASGI app is not set up"

        logger.debug(f"Handling A2A message with payload: {message}")

        body = message.payload
        route_path = (
            message.route_path
            if message.route_path.startswith("/")
            else f"/{message.route_path}"
        )
        method = message.method

        # check if the body is a JSONRPCSuccessResponse, and if so, convert it to a SendMessageRequest
        try:
            inner = JSONRPCSuccessResponse.model_validate_json(body)
            msg_params = {"message": inner.result}
            request = SendMessageRequest(
                id=str(uuid4()), params=MessageSendParams(**msg_params)
            )
            body = json.dumps(
                request.model_dump(mode="json", exclude_none=True)
            ).encode("utf-8")
        except Exception:
            pass

        headers = []
        for key, value in message.headers.items():
            if isinstance(value, str):
                headers.append((key.encode("utf-8"), value.encode("utf-8")))
            elif isinstance(value, bytes):
                headers.append((key.encode("utf-8"), value))
            else:
                raise ValueError(f"Unsupported header value type: {type(value)}")

        # Check for Authorization (case-insensitive)
        auth_value = message.headers.get("Authorization") or message.headers.get(
            "authorization"
        )
        if auth_value:
            headers.append((b"authorization", auth_value.encode("utf-8")))
        else:
            # Ensure authorization header is present to avoid issues with some ASGI A2A apps
            headers.append((b"authorization", b""))

        # Set up ASGI scope
        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": route_path,
            "raw_path": route_path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "client": ("agntcy-bridge", 0),
            "server": ("agntcy-bridge", 0),
        }

        # Create the receive channel that will yield request body
        async def receive() -> Dict[str, Any]:
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        # Create the send channel that will receive responses
        response_data = {
            "status": None,
            "headers": None,
            "body": bytearray(),
        }

        async def send(message: Dict[str, Any]) -> None:
            message_type = message["type"]

            if message_type == "http.response.start":
                response_data["status"] = message["status"]
                response_data["headers"] = message.get("headers", [])

            elif message_type == "http.response.body":
                if "body" in message:
                    response_data["body"].extend(message["body"])

        # Call the ASGI application with our scope, receive, and send
        try:
            await self._app(scope, receive, send)

            # Parse the body
            body = bytes(response_data["body"])
            try:
                body_obj = json.loads(body.decode("utf-8"))
                payload = json.dumps(body_obj).encode("utf-8")  # re-encode as bytes
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = body  # raw bytes

            return Message(
                type="A2AResponse",
                payload=payload,
                reply_to=message.reply_to,
            )
        except Exception as e:
            # Create error response message when callback function throws an error
            error_response = {
                "error": "true",
                "error_type": "callback_error",
                "error_message": str(e),
                "status": "error",
            }
            error_payload = json.dumps(error_response).encode("utf-8")

            return Message(
                type="A2AResponse",
                payload=error_payload,
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
        """Full lifecycle: transport.setup() → set_callback() → subscribe() → directory → protocol.setup()."""
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
                "Overwriting card.url '%s' → '%s' for %s transport",
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

        # Protocol-level setup (ASGI bridge, tracing, etc.)
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
