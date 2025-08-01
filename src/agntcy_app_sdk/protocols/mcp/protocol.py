# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Callable, Dict, List, Tuple, Union
import os
import datetime
import json

from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol

from contextlib import asynccontextmanager
from starlette.types import Scope
from mcp import ClientSession
from mcp.server.lowlevel import Server as MCPServer
from mcp.shared.message import SessionMessage
import anyio
from anyio import Event
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
import logging

# Set up logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# configure_logging()
# logger = get_logger(__name__)


class MCPProtocol(BaseAgentProtocol):
    """
    MPCProtocol creates an MCP client session with a specified transport and URL.
    It should define methods to create clients, receivers, and handle messages.
    """

    def type(self):
        return "MCP"

    @asynccontextmanager
    async def create_client(
        self,
        topic: str,
        url: str = None,
        transport: BaseTransport = None,
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=15),
        message_retries: int = 2,
        **kwargs,
    ) -> ClientSession:
        """
        Create a client for the MCP protocol.
        """
        self.message_timeout = message_timeout
        self.message_retries = message_retries

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            pass

        # create streams
        async with self.new_streams(transport, topic) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, **kwargs
            ) as mcp_session:
                yield mcp_session

    def create_server_topic(self):
        raise NotImplementedError(
            "MCPProtocol does not implement create_server_topic method"
        )

    @asynccontextmanager
    async def new_streams(self, transport: BaseTransport, topic: str, **kwargs):
        """
        Create new streams for the MCP protocol that map to a transport.
        """
        # initialize streams
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
        read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
        write_stream: MemoryObjectSendStream[SessionMessage]
        write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        async def reader():
            try:
                async for message in read_stream:
                    pass
            except Exception as e:
                logger.error(f"Error reading from stream: {e}")
                raise

        async def writer():
            try:
                async for session_message in write_stream_reader:
                    try:
                        msg_dict = session_message.message.model_dump(
                            by_alias=True, mode="json", exclude_none=True
                        )
                        await transport.publish(
                            topic=topic,
                            respond=False,
                            message=Message(
                                type="MCPJSONRPCMessage",
                                payload=json.dumps(msg_dict),
                                route_path="mcp/",
                                method="POST",
                                headers={
                                    "accept": "text/event-stream, application/json",
                                    "content-type": "application/json",
                                },
                            ),
                        )
                        print("Message published")
                    except Exception:
                        logger.error("Error sending message", exc_info=True)
                        raise
            finally:
                await write_stream_reader.aclose()

        async with anyio.create_task_group() as tg:
            tg.start_soon(reader)
            tg.start_soon(writer)

            try:
                yield read_stream, write_stream
            finally:
                # cancel the task group
                tg.cancel_scope.cancel()
                # delete the session
                logger.info(f"Closing MCP session for topic {topic}")
                await read_stream_writer.aclose()
                await write_stream.aclose()

    def create_ingress_handler(
        self, server: MCPServer, transport: BaseTransport, topic: str, **kwargs
    ) -> Callable[[Message], None]:
        """
        Pre-creates and reuses a stream pair for a given topic.
        Returns an async handler to feed inbound messages into the session.
        """
        # prepare the async context once
        stream_cm = self.new_streams(transport, topic, **kwargs)
        setup_complete = Event()
        read_stream = None
        write_stream = None

        async def session_runner():
            nonlocal read_stream, write_stream
            async with stream_cm as (r, w):
                read_stream, write_stream = r, w
                setup_complete.set()
                # hand off to server session logic until completion
                await server.run(read_stream, write_stream, topic=topic)

        # start the session runner in background
        anyio.create_task_group().start_soon(session_runner)

        # the handler that NATS (or other transport) will call
        async def handler(message: Message) -> None:
            await setup_complete.wait()
            try:
                # convert transport Message to SessionMessage
                session_msg = SessionMessage.from_transport(message)
                await read_stream.send(session_msg)
            except Exception:
                logger.exception("Error routing inbound MCP message")

        return handler

    '''def create_ingress_handler(self, server: MCPServer) -> Callable[[Message], Message]:
        """
        Create an ingress handler for the MCP protocol.
        """
        # create a streamable HTTP app for the MCP server, enables both HTTP and transport
        # communication
        import inspect
        # inspect the signature of server.run
        print(inspect.signature(server.run))
        self._app = server.streamable_http_app()
        self._is_started = False
        self._lifespan_manager = LifespanManager(self._app)
        return self.handle_incoming_request'''

    async def handle_incoming_request(self, message: Message) -> Message:
        """
        Handle incoming MCP protocol requests by routing them through the ASGI app.

        Args:
            message: The incoming message to process

        Returns:
            The processed response message

        Raises:
            RuntimeError: If the ASGI app is not initialized
            ValueError: If header values have unsupported types
        """
        if self._app is None:
            raise RuntimeError(
                "ASGI app is not set up. Call create_ingress_handler first."
            )

        try:
            # Start the lifespan manager once if not already started
            if not self._is_started:
                await self._lifespan_manager.__aenter__()
                self._is_started = True

            scope = self._build_asgi_scope(message)
            receive_callable = self._create_receive_callable(message.payload)
            response_data = await self._process_asgi_request(scope, receive_callable)

            return self._build_response_message(response_data, message.reply_to)

        except Exception as e:
            logger.error(f"Error processing MCP request: {e}")
            raise

    def _normalize_route_path(self, route_path: str) -> str:
        """
        Normalize the route path to ensure it starts and ends with '/'.

        Args:
            route_path: The original route path

        Returns:
            The normalized route path
        """
        if not route_path.startswith("/"):
            route_path = f"/{route_path}"
        if not route_path.endswith("/"):
            route_path += "/"
        return route_path

    def _build_headers(
        self, headers_dict: Dict[str, Union[str, bytes]]
    ) -> List[Tuple[bytes, bytes]]:
        """
        Convert headers dictionary to ASGI format.

        Args:
            headers_dict: Dictionary of headers

        Returns:
            List of header tuples in ASGI format

        Raises:
            ValueError: If header value type is not supported
        """
        headers = []

        for key, value in headers_dict.items():
            key_bytes = key.encode("utf-8")

            if isinstance(value, str):
                value_bytes = value.encode("utf-8")
            elif isinstance(value, bytes):
                value_bytes = value
            else:
                raise ValueError(
                    f"Unsupported header value type: {type(value)}. "
                    f"Expected str or bytes, got {type(value).__name__}"
                )

            headers.append((key_bytes, value_bytes))

        return headers

    def _build_asgi_scope(self, message: Message) -> Scope:
        """
        Build ASGI scope from the incoming message.

        Args:
            message: The incoming message

        Returns:
            ASGI scope dictionary
        """
        route_path = self._normalize_route_path(message.route_path)
        headers = self._build_headers(message.headers)

        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1",
            "method": message.method,
            "scheme": "http",
            "path": route_path,
            "raw_path": route_path.encode("utf-8"),
            "query_string": b"",
            "headers": headers,
            "client": ("agntcy-bridge", 0),
            "server": ("agntcy-bridge", 0),
        }

    def _create_receive_callable(self, body: bytes):
        """
        Create the ASGI receive callable.

        Args:
            body: The request body

        Returns:
            Async callable for ASGI receive interface
        """

        async def receive() -> Dict[str, Any]:
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        return receive

    def _create_send_callable(self, response_data: Dict[str, Any]):
        """
        Create the ASGI send callable that captures response data.

        Args:
            response_data: Dictionary to store response data

        Returns:
            Async callable for ASGI send interface
        """

        async def send(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_data["status"] = message["status"]
                response_data["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_data["body"].extend(message.get("body", b""))

        return send

    async def _process_asgi_request(
        self, scope: Scope, receive: Callable
    ) -> Dict[str, Any]:
        """
        Process the ASGI request and capture the response.

        Args:
            scope: ASGI scope
            receive: ASGI receive callable

        Returns:
            Dictionary containing response data
        """
        response_data = {
            "status": None,
            "headers": None,
            "body": bytearray(),
        }

        send = self._create_send_callable(response_data)

        # The lifespan is already managed, just call the app directly
        await self._app(scope, receive, send)

        return response_data

    def _process_response_body(self, raw_body: bytes) -> bytes:
        """
        Process the raw response body, attempting JSON parsing if possible.

        Args:
            raw_body: The raw response body bytes

        Returns:
            Processed response body
        """
        try:
            # Attempt to parse and re-serialize JSON for consistency
            json_data = json.loads(raw_body.decode("utf-8"))
            return json.dumps(json_data).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Return raw body if JSON parsing fails
            logger.debug("Response body is not valid JSON, returning raw bytes")
            return raw_body

    def _build_response_message(
        self, response_data: Dict[str, Any], reply_to: str
    ) -> Message:
        """
        Build the response message from ASGI response data.

        Args:
            response_data: The captured response data
            reply_to: The reply-to identifier

        Returns:
            The response message
        """
        raw_body = bytes(response_data["body"])
        processed_payload = self._process_response_body(raw_body)

        # Log response details for debugging
        logger.info(f"Response status: {response_data['status']}")
        logger.info(f"Response headers: {response_data['headers']}")
        logger.info(f"Response body {processed_payload}")

        return Message(
            type="MCPJSONRPCMessage",
            payload=processed_payload,
            reply_to=reply_to,
        )

    def message_translator(self, request: Any) -> Message:
        raise NotImplementedError(
            "Message translation is not implemented for MCP protocol"
        )
