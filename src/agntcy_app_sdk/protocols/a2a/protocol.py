# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from starlette.types import Scope
from typing import Dict, Any, Callable
import json
from uuid import uuid4
import httpx
import os

from a2a.client import A2AClient, A2ACardResolver
from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard, SendMessageRequest, SendMessageResponse

from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message
from opentelemetry.instrumentation.starlette import StarletteInstrumentor

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

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

    async def get_client_from_agent_card_topic(
        self, topic: str, transport: BaseTransport
    ) -> A2AClient:
        """
        Create an A2A client from the agent card topic, bypassing all need for a URL.
        """
        logger.info(f"Getting agent card from topic {topic}")

        path = ".well-known/agent.json"
        method = "GET"

        request = Message(
            type="A2ARequest",
            payload=json.dumps({"path": path, "method": method}),
            route_path=path,
            method=method,
        )

        response = await transport.publish(
            topic,
            request,
            respond=True,
        )

        response.payload = json.loads(response.payload.decode("utf-8"))
        card = AgentCard.model_validate(response.payload)

        cl = A2AClient(
            agent_card=card,
            httpx_client=None,
            url=None,
        )
        cl.agent_card = card
        return cl

    async def create_client(
        self,
        url: str = None,
        topic: str = None,
        transport: BaseTransport = None,
        **kwargs,
    ) -> A2AClient:
        """
        Create an A2A client, overriding the default client _send_request method to
        use the provided transport.
        """
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            # Initialize tracing if enabled
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()
            logger.info("A2A Instrumentor enabled for tracing")

        if url is None and topic is None:
            raise ValueError("Either url or topic must be provided")

        # if a transport and a topic are provided, bypass the URL and use the topic
        if topic and transport:
            client = await self.get_client_from_agent_card_topic(topic, transport)
        else:
            httpx_client = httpx.AsyncClient()
            client = await A2AClient.get_client_from_agent_card_url(httpx_client, url)
            # ensure the client has an agent card
            if not hasattr(client, "agent_card"):
                agent_card = await A2ACardResolver(
                    httpx_client,
                    base_url=url,
                ).get_agent_card()
                client.agent_card = agent_card

        if transport:
            logger.info(
                f"Using transport {transport.type()} for A2A client {topic or client.agent_card.name}"
            )
            if not topic:
                topic = self.create_agent_topic(client.agent_card)

            # override the _send_request method to use the provided transport
            self._override_send_methods(client, transport, topic)

        return client

    def _override_send_methods(
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

            try:
                response = await transport.publish(
                    topic,
                    self.message_translator(
                        request=rpc_request_payload, headers=headers
                    ),
                    respond=True,
                )

                response.payload = json.loads(response.payload.decode("utf-8"))
                return response.payload
            except Exception as e:
                logger.error(
                    f"Error sending A2A request with transport {transport.type()}: {e}"
                )
                raise e  # TODO: handle errors more gracefully

        async def broadcast_message(
            request: SendMessageRequest,
            expected_responses: int = 1,
            timeout: float = 10.0,
        ) -> dict[str, Any]:
            """
            Broadcast a request using the provided transport.
            """
            if not request.id:
                request.id = str(uuid4())

            msg = self.message_translator(
                request=request.model_dump(mode="json", exclude_none=True)
            )

            try:
                responses = await transport.broadcast(
                    topic,
                    msg,
                    expected_responses=expected_responses,
                    timeout=timeout,
                )
            except Exception as e:
                logger.error(
                    f"Error broadcasting A2A request with transport {transport.type()}: {e}"
                )
                return []

            broadcast_responses = []
            for raw_resp in responses:
                try:
                    resp = json.loads(raw_resp.payload.decode("utf-8"))
                    broadcast_responses.append(SendMessageResponse(resp))
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue

            return broadcast_responses

        # override the _send_request method to use the provided transport
        client._send_request = _send_request
        client.broadcast_message = broadcast_message

    def message_translator(
        self, request: dict[str, Any], headers: dict[str, Any] | None = None
    ) -> Message:
        """
        Translate an A2A request into the internal Message object.
        """
        if headers is None:
            headers = {}
        if not isinstance(headers, dict):
            raise ValueError("Headers must be a dictionary")

        message = Message(
            type="A2ARequest",
            payload=json.dumps(request),
            route_path="/",  # json-rpc path
            method="POST",  # A2A json-rpc will always use POST
            headers=headers,
        )

        return message

    def create_ingress_handler(
        self, server: A2AStarletteApplication
    ) -> Callable[[Message], Message]:
        """
        Create a bridge between the A2A server/ASGI app and our internal message type.
        """
        # Create an ASGI adapter
        self._app = server.build()

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()
            StarletteInstrumentor().instrument_app(self._app)
            logger.info("A2A ASGI app instrumented for tracing")

        return self.handle_incoming_request

    async def handle_incoming_request(self, message: Message) -> Message:
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

        headers = []
        for key, value in message.headers.items():
            if isinstance(value, str):
                headers.append((key.encode("utf-8"), value.encode("utf-8")))
            elif isinstance(value, bytes):
                headers.append((key.encode("utf-8"), value))
            else:
                raise ValueError(f"Unsupported header value type: {type(value)}")

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


def get_trace_id_from_traceparent(traceparent_header: str) -> str | None:
    import re

    """
    Extracts the trace-id from a W3C traceparent header string.

    Args:
        traceparent_header: The full traceparent header string (e.g.,
                            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01").

    Returns:
        The trace-id as a string, or None if the format is invalid.
    """
    if not traceparent_header:
        return None

    # Regex to match the traceparent format
    # Group 1: version (00)
    # Group 2: trace-id (16-byte hex)
    # Group 3: parent-id (8-byte hex)
    # Group 4: trace-flags (1-byte hex)
    match = re.match(
        r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
        traceparent_header,
    )

    if match:
        return match.group(2)
    else:
        return None
