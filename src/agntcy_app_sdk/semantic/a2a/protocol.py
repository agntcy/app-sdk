# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from starlette.types import Scope
from typing import Dict, Any, List, AsyncIterator
import json
from uuid import uuid4
import httpx
import os

from a2a.client import A2AClient, A2ACardResolver
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH, PREV_AGENT_CARD_WELL_KNOWN_PATH
from a2a.server.apps import A2AStarletteApplication
from a2a.types import (
    AgentCard,
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendMessageResponse,
    JSONRPCSuccessResponse,
    MessageSendParams,
)
from agntcy_app_sdk.semantic.base import BaseAgentProtocol
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.message import Message
from opentelemetry.instrumentation.starlette import StarletteInstrumentor

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def get_client_from_agent_card_url(
    httpx_client: httpx.AsyncClient,
    base_url: str,
    http_kwargs: dict[str, Any] | None = None,
) -> A2AClient:
    """
    Replacement for removed get_client_from_agent_card_url().
    Tries both agent-card.json (v0.3.0) and legacy agent.json.
    """
    try:
        agent_card: AgentCard = await A2ACardResolver(
            httpx_client,
            base_url=base_url,
            agent_card_path=AGENT_CARD_WELL_KNOWN_PATH,
        ).get_agent_card(http_kwargs=http_kwargs)
    except Exception as e:
        logger.info(
            f"Failed to get client from agent card url with v3 path, "
            f"falling back to v2 path: {e}"
        )
        try:
            agent_card: AgentCard = await A2ACardResolver(
                httpx_client,
                base_url=base_url,
                agent_card_path=PREV_AGENT_CARD_WELL_KNOWN_PATH,
            ).get_agent_card(http_kwargs=http_kwargs)
        except Exception as e:
            logger.error(
                f"Failed to get client from agent card url with v2 " f"path: {e}"
            )
            raise e

    return A2AClient(httpx_client=httpx_client, agent_card=agent_card)


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

        method = "GET"

        # Try v3 path first, fall back to v2 if anything goes wrong
        try:
            request = Message(
                type="A2ARequest",
                payload=json.dumps(
                    {"path": AGENT_CARD_WELL_KNOWN_PATH, "method": method}
                ),
                route_path=AGENT_CARD_WELL_KNOWN_PATH,
                method=method,
            )
            response = await transport.request(topic, request)

            response.payload = json.loads(response.payload.decode("utf-8"))
            card = AgentCard.model_validate(response.payload)
        except Exception as e:
            logger.info(
                f"A2A v3 path failed or invalid payload, falling back to v2: {e}"
            )

            request = Message(
                type="A2ARequest",
                payload=json.dumps(
                    {"path": PREV_AGENT_CARD_WELL_KNOWN_PATH, "method": method}
                ),
                route_path=PREV_AGENT_CARD_WELL_KNOWN_PATH,
                method=method,
            )
            response = await transport.request(topic, request)

            response.payload = json.loads(response.payload.decode("utf-8"))
            card = AgentCard.model_validate(response.payload)

        cl = A2AClient(
            agent_card=card,
            httpx_client=None,  # Set the httpx_client to None so it will use the overridden version of _send_request() below
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

        if transport:
            await transport.setup()

        # if a transport and a topic are provided, bypass the URL and use the topic
        if topic and transport:
            client = await self.get_client_from_agent_card_topic(topic, transport)
        else:
            httpx_client = httpx.AsyncClient()
            try:
                client = await get_client_from_agent_card_url(httpx_client, url)
            except Exception as e:
                logger.error(f"Failed to retrieve A2A client: {e}")
                raise e

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
                response = await transport.request(
                    topic,
                    self.message_translator(
                        request=rpc_request_payload, headers=headers
                    ),
                )

                response.payload = json.loads(response.payload.decode("utf-8"))
                return response.payload
            except Exception as e:
                logger.error(
                    f"Error sending A2A request with transport {transport.type()}: {e}"
                )
                raise e  # TODO: handle errors more gracefully

        async def start_groupchat(
            init_message: SendMessageRequest,
            group_channel: str,
            participants: List[str],
            timeout: float = 60,
            end_message: str = "work-done",
        ) -> List[SendMessageResponse]:
            if not init_message.id:
                init_message.id = str(uuid4())

            msg = self.message_translator(
                request=init_message.model_dump(mode="json", exclude_none=True)
            )
            try:
                member_messages = await transport.start_conversation(
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
                        groupchat_messages.append(SendMessageResponse(resp))
                    except Exception as e:
                        logger.error(f"Error decoding JSON response: {e}")
                        continue

                return groupchat_messages
            except Exception as e:
                logger.error(
                    f"Error starting group chat A2A request with transport {transport.type()}: {e}"
                )
                return []

        async def start_streaming_groupchat(
            init_message: SendMessageRequest,
            group_channel: str,
            participants: List[str],
            timeout: float = 60,
            end_message: str = "work-done",
        ) -> AsyncIterator[SendMessageResponse]:
            if not init_message.id:
                init_message.id = str(uuid4())

            msg = self.message_translator(
                request=init_message.model_dump(mode="json", exclude_none=True)
            )

            async for raw_member_message in transport.start_streaming_conversation(
                group_channel=group_channel,
                participants=participants,
                init_message=msg,
                end_message=end_message,
                timeout=timeout,
            ):
                message = json.loads(raw_member_message.payload.decode("utf-8"))
                yield SendMessageResponse(message)

        async def broadcast_message_streaming(
            request: SendStreamingMessageRequest,
            recipients: List[str] | None = None,
            broadcast_topic: str = None,
            timeout: float = 60.0,
        ) -> AsyncIterator[SendMessageResponse]:
            """
            Broadcast a streaming request using the provided transport.
            """
            if not request.id:
                request.id = str(uuid4())

            msg = self.message_translator(
                request=request.model_dump(mode="json", exclude_none=True)
            )

            if not broadcast_topic:
                broadcast_topic = topic

            try:
                async for raw_resp in transport.gather_stream(
                    broadcast_topic,
                    msg,
                    recipients=recipients,
                    timeout=timeout,
                ):
                    try:
                        resp = json.loads(raw_resp.payload.decode("utf-8"))
                        yield SendMessageResponse(resp)
                    except Exception as e:
                        logger.error(f"Error decoding JSON response: {e}")
                        continue
            except Exception as e:
                logger.error(
                    f"Error gathering streaming A2A request with transport {transport.type()}: {e}"
                )
                return

        async def broadcast_message(
            request: SendMessageRequest | SendStreamingMessageRequest,
            recipients: List[str] | None = None,
            broadcast_topic: str = None,
            timeout: float = 60.0,
        ) -> List[SendMessageResponse]:
            """
            Broadcast a request using the provided transport.
            """
            if not request.id:
                request.id = str(uuid4())

            msg = self.message_translator(
                request=request.model_dump(mode="json", exclude_none=True)
            )

            if not broadcast_topic:
                broadcast_topic = topic

            try:
                responses = await transport.gather(
                    broadcast_topic,
                    msg,
                    recipients=recipients,
                    timeout=timeout,
                )
            except Exception as e:
                logger.error(
                    f"Error gathering A2A request with transport {transport.type()}: {e}"
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
        client._transport._send_request = _send_request

        # monkey patch experimental patterns
        client.broadcast_message = broadcast_message
        client.broadcast_message_streaming = broadcast_message_streaming
        client.start_groupchat = start_groupchat
        client.start_streaming_groupchat = start_streaming_groupchat

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

    def bind_server(self, server: A2AStarletteApplication) -> None:
        """Bind the protocol to a server."""
        self._server = server

    def setup_ingress_handler(self) -> None:
        """
        Create a bridge between the A2A server/ASGI app and our internal message type.
        """

        if not self._server:
            raise ValueError(
                "A2A server is not bound to the protocol, please bind it first"
            )

        # Create an ASGI adapter
        self._app = self._server.build()

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor

            A2AInstrumentor().instrument()
            StarletteInstrumentor().instrument_app(self._app)
            logger.info("A2A ASGI app instrumented for tracing")

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
