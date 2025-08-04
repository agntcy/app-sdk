# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Callable, Dict
import os
import datetime
import json

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol

from contextlib import asynccontextmanager
from asgi_lifespan import LifespanManager
from starlette.types import Scope
from mcp import ClientSession
from mcp.server.lowlevel import Server as MCPServer
from mcp.shared.message import SessionMessage
import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

configure_logging()
logger = get_logger(__name__)

MCP_SESSION_ID = "mcp-session-id"

class FastMCPProtocol(BaseAgentProtocol):
    """
    FastMCPProtocol creates an MCP client session with a specified transport and URL.
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
        '''async with self.new_streams(transport, topic) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, **kwargs
            ) as mcp_session:
                yield mcp_session'''

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

        self._read_stream = read_stream
        self._write_stream = write_stream

        async def writer():
            try:
                async for session_message in write_stream_reader:
                    try:
                        print(f"Sending message: {session_message}")
                        msg_dict = session_message.message.model_dump(by_alias=True, mode="json", exclude_none=True)
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
                                    MCP_SESSION_ID: "this is a test session"
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
            #tg.start_soon(reader)
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
        self, server: MCPServer, transport: BaseTransport, topic: str = None
    ) -> Callable[[Message], Message]:
        """
        Create an ingress handler for the MCP protocol.
        """
        # create a streamable HTTP app for the MCP server, enables both HTTP and transport
        # communication
        self._server = server
        #self._app = server.streamable_http_app()

        ''''async with self.new_streams(transport, topic) as (read_stream, write_stream):
            await self._mcp_server.run(
                read_stream,
                write_stream,
                self._mcp_server.create_initialization_options(),
            )'''

        return self.handle_incoming_request
    
    async def handle_simple_incoming_request(self, message: Message):
        # write the message to the read stream
        await self._read_stream.send(message)

    async def handle_incoming_request(self, message: Message) -> Message:
        assert self._app is not None, "ASGI app is not set up"

        route_path = (
            message.route_path
            if message.route_path.startswith("/")
            else f"/{message.route_path}"
        )
        if not route_path.endswith("/"):
            route_path += "/"

        body = message.payload
        method = message.method

        headers = []
        for key, value in message.headers.items():
            if isinstance(value, str):
                headers.append((key.encode("utf-8"), value.encode("utf-8")))
            elif isinstance(value, bytes):
                headers.append((key.encode("utf-8"), value))
            else:
                raise ValueError(f"Unsupported header value type: {type(value)}")

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

        async def receive() -> Dict[str, Any]:
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        response_data = {
            "status": None,
            "headers": None,
            "body": bytearray(),
        }

        async def send(message: Dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                response_data["status"] = message["status"]
                response_data["headers"] = message.get("headers", [])
            elif message["type"] == "http.response.body":
                response_data["body"].extend(message.get("body", b""))

        # 👇 Lifespan context actually triggers self.session_manager.run()
        async with LifespanManager(self._app):
            await self._app(scope, receive, send)

        raw_body = bytes(response_data["body"])
        try:
            payload = json.dumps(json.loads(raw_body.decode("utf-8"))).encode("utf-8")
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = raw_body

        print(f"Response status: {response_data['status']}")
        print(f"Response headers: {response_data['headers']}")
        print(f"Response body: {payload}")

        return Message(
            type="MCPJSONRPCMessage",
            payload=payload,
            reply_to=message.reply_to,
        )

    def message_translator(self, request: Any) -> Message:
        raise NotImplementedError(
            "Message translation is not implemented for MCP protocol"
        )