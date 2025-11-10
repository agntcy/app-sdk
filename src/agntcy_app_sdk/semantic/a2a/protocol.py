# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Dict, Any
import json
from uuid import uuid4
import httpx
import os

from a2a.client import A2AClient, A2ACardResolver
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from a2a.server.apps import A2AStarletteApplication

from starlette.types import Scope
from a2a.types import (
    AgentCard,
    SendMessageRequest,
    JSONRPCSuccessResponse,
    MessageSendParams, HTTPAuthSecurityScheme, SecurityScheme,
)
from agntcy_app_sdk.semantic.base import BaseAgentProtocol
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.semantic.a2a.utils import (
    get_client_from_agent_card_url,
    get_client_from_agent_card_topic,
    message_translator,
)
from agntcy_app_sdk.semantic.a2a.experimental import (
    experimental_a2a_transport_methods,
)

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.common.auth import is_identity_auth_enabled

from identityservice.auth.starlette import IdentityServiceA2AMiddleware
from identityservice.sdk import IdentityServiceSdk

configure_logging()
logger = get_logger(__name__)


class A2AProtocol(BaseAgentProtocol):
    def type(self):
        return "A2A"

    @staticmethod
    def create_agent_topic(agent_card: AgentCard) -> str:
        """
        A standard way to create a topic for the agent card metadata.
        """
        return f"{agent_card.name}_{agent_card.version}"

    async def create_client(
        self,
        url: str = None,
        topic: str = None,
        transport: BaseTransport = None,
        add_experimental_patterns: bool = True,
        **kwargs,
    ) -> A2AClient:
        """
        Create an A2A client with optional custom transport.

        Args:
            url: Agent card URL (required if topic not provided)
            topic: Agent topic (required if url not provided)
            transport: Custom transport implementation
            add_experimental_patterns: Whether to add experimental transport methods
            **kwargs: Additional arguments (currently unused)

        Returns:
            Configured A2AClient instance

        Raises:
            ValueError: If neither url nor topic is provided
        """
        self._initialize_tracing_if_enabled()
        self._validate_inputs(url, topic)

        if transport:
            await transport.setup()

        client = await self._create_base_client(url, topic, transport)

        if transport:
            effective_topic = topic or self.create_agent_topic(client.agent_card)
            self._configure_transport(client, transport, effective_topic)

        if add_experimental_patterns:
            self._add_experimental_patterns(client, transport, topic)

        return client

    def _initialize_tracing_if_enabled(self) -> None:
        """Initialize OpenTelemetry tracing if enabled via environment variable."""
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()
            logger.info("A2A Instrumentor enabled for tracing")

    def _validate_inputs(self, url: str, topic: str) -> None:
        """Ensure at least one of url or topic is provided."""
        if url is None and topic is None:
            raise ValueError("Either url or topic must be provided")

    async def _create_base_client(
        self,
        url: str,
        topic: str,
        transport: BaseTransport,
    ) -> A2AClient:
        """
        Create the base A2A client from either topic or URL.

        Args:
            url: Agent card URL
            topic: Agent topic
            transport: Custom transport (used with topic-based creation)

        Returns:
            A2AClient instance with agent card attached
        """
        if topic and transport:
            return await get_client_from_agent_card_topic(topic, transport)

        return await self._create_client_from_url(url)

    async def _create_client_from_url(self, url: str) -> A2AClient:
        """Create client from agent card URL and ensure agent card is attached."""
        httpx_client = httpx.AsyncClient()

        try:
            client = await get_client_from_agent_card_url(httpx_client, url)
        except Exception as e:
            logger.error(f"Failed to retrieve A2A client from URL '{url}': {e}")
            raise

        # Ensure agent card is attached
        if not hasattr(client, "agent_card"):
            agent_card = await A2ACardResolver(
                httpx_client,
                base_url=url,
            ).get_agent_card()
            client.agent_card = agent_card

        return client

    def _configure_transport(
        self,
        client: A2AClient,
        transport: BaseTransport,
        topic: str,
    ) -> None:
        """Configure client to use custom transport for requests."""
        logger.info(
            f"Using transport {transport.type()} for A2A client "
            f"'{topic or client.agent_card.name}'"
        )
        self._register_transport(client, transport, topic)

    def _add_experimental_patterns(
        self,
        client: A2AClient,
        transport: BaseTransport,
        topic: str,
    ) -> None:
        """Dynamically add experimental transport methods to client."""
        logger.info("Adding experimental A2A transport patterns to client")

        experimental_methods = experimental_a2a_transport_methods(transport, topic)
        for method_name, method in experimental_methods.items():
            setattr(client, method_name, method)

    def _register_transport(
        self, client: A2AClient, transport: BaseTransport, topic: str
    ) -> None:
        """
        Register the send methods for the A2A client.
        """

        async def _send_request(
                rpc_request_payload: dict[str, Any],
                http_kwargs: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """
            Send a request using the provided transport.
            """

            if http_kwargs is None:
                http_kwargs = {}
            headers = http_kwargs.get("headers", {})

            if is_identity_auth_enabled():
                try:
                    access_token = IdentityServiceSdk().access_token()
                    logger.info(f"Obtained access token for agent: {access_token}")
                    if access_token:
                        headers["Authorization"] = f"Bearer {access_token}"
                except Exception as e:
                    logger.error(f"Failed to get access token for agent: {e}")

            try:
                response = await transport.request(
                    topic,
                    message_translator(request=rpc_request_payload, headers=headers),
                )

                response.payload = json.loads(response.payload.decode("utf-8"))

                # Handle Identity Middleware AuthN error messages
                if "error" in response.payload and "reason" in response.payload:
                    if response.payload["error"] == "unauthorized":
                        raise ValueError(f"Unauthorized: {response.payload['reason']}")

                return response.payload

            except Exception as e:
                logger.error(
                    f"Error sending A2A request with transport {transport.type()}: {e}"
                )
                raise

        # override the _send_request method to use the provided transport
        client._transport._send_request = _send_request

    def bind_server(self, server: A2AStarletteApplication) -> None:
        """Bind the protocol to a server."""
        self._server = server

    async def setup(self, *args, **kwargs) -> None:
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
        self._server.agent_card.security_schemes = {AUTH_SCHEME: SecurityScheme(root=auth_scheme)}
        self._server.agent_card.security = [{AUTH_SCHEME: ["*"]}]

        self._app = self._server.build()
        self._app.add_middleware(
            IdentityServiceA2AMiddleware, # Define the middleware
            agent_card=self._server.agent_card,
            public_paths=[AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH],
        )

    async def handle_message(self, message: Message) -> Message:
        """
        Handle an incoming request and return a response.
        """
        assert self._app is not None, "ASGI app is not set up"

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
        auth_value = message.headers.get("Authorization") or message.headers.get("authorization")
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
