# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Callable
import os
import datetime
import json

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocol

from mcp import ClientSession
import mcp.types as types
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

from contextlib import asynccontextmanager
import asyncio
import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

configure_logging()
logger = get_logger(__name__)

class MCPProtocol(BaseAgentProtocol):
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

        async def send_method(session_message: SessionMessage):
            return await self._client_send(transport, session_message, topic)

        # create streams
        async with self.new_streams(send_method) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, **kwargs
            ) as mcp_session:
                yield mcp_session

    def create_server_topic(self):
        raise NotImplementedError(
            "MCPProtocol does not implement create_server_topic method"
        )

    @asynccontextmanager
    async def new_streams(self, send_method: Callable, **kwargs):
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
        self.read_stream_writer = read_stream_writer

        async def reader():
            try:
                async for message in read_stream:
                    await read_stream_writer.send(message)
            except Exception as e:
                logger.error(f"Error reading from stream: {e}")
                raise

        async def writer():
            try:
                async for session_message in write_stream_reader:
                    try:
                        resp = await send_method(session_message)
                        if resp:
                            msg = resp.payload.decode()
                            json_rpc_message = types.JSONRPCMessage.model_validate_json(msg)
                            await self.read_stream_writer.send(SessionMessage(json_rpc_message))
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
                logger.info(f"Closing MCP session streams.")
                await read_stream_writer.aclose()
                await write_stream.aclose()

    async def _client_send(self, transport, session_message, topic):
        msg_dict = session_message.message.model_dump(by_alias=True, mode="json", exclude_none=True)
        resp = await transport.publish(
            topic=topic,
            respond=True,
            message=Message(
                type="MCPJSONRPCMessage",
                payload=json.dumps(msg_dict),
            ),
        )

        if not resp:
            raise ValueError("No response received from MCP server")
        
        return resp

    def bind_server(self, server: MCPServer | FastMCP) -> None:
        """Bind the protocol to a server."""
        if not isinstance(server, (MCPServer, FastMCP)):
            raise ValueError("Server must be an instance of MCPServer or FastMCP")
        
        if isinstance(server, FastMCP):
            self._low_level_server = server._mcp_server
        else:
            self._low_level_server = server

    def bind_transport(self, transport: BaseTransport) -> None:
        self._transport = transport

    async def create_ingress_handler(
        self, topic: str = None
    ) -> None:
        """
        Create an ingress handler for the MCP protocol.
        """
        self._response_futures: dict[str, asyncio.Future] = {}

        async def reply_method(session_message: SessionMessage):
            request_id = session_message.message.root.id
            if request_id in self._response_futures:
                self._response_futures[request_id].set_result(session_message)
            else:
                logger.warning(f"Unexpected response for id={request_id}")

        async with self.new_streams(reply_method) as (read_stream, write_stream):
            await self._low_level_server.run(
                read_stream,
                write_stream,
                self._low_level_server.create_initialization_options(),
                stateless=True
            )

            logger.info("[setup] MCP server started successfully.")

    async def handle_incoming_request(self, message: Message):
        # write the message to the read stream
        rpc_message = types.JSONRPCMessage.model_validate_json(message.payload.decode())

        future = asyncio.get_event_loop().create_future()
        self._response_futures[rpc_message.root.id] = future

        session_message = SessionMessage(rpc_message)
        await self.read_stream_writer.send(session_message)

        try:
            response = await asyncio.wait_for(future, timeout=10)
            return Message(
                type="MCPJSONRPCMessage",
                payload=json.dumps(response.message.model_dump(by_alias=True, mode="json", exclude_none=True))
            )
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for response for id={rpc_message.root.id}")
            raise TimeoutError(f"Timeout waiting for response for id={rpc_message.root.id}")
        except Exception as e:
            logger.error(f"Error waiting for response: {e}")
            raise e

    def message_translator(self, request: Any) -> Message:
        """
        Translate a request into a Message object.
        This method should be implemented to convert the request format
        into the Message format used by the MCP protocol.
        """
        raise NotImplementedError(
            "Message translation is not implemented for MCP protocol"
        )