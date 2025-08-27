# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Callable
import os
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
    MCPProtocol implements the BaseAgentProtocol to bridge MCP client and server sessions
    with a generic transport layer.

    Key responsibilities:
    - Create and manage MCP client sessions with bidirectional streams
    - Handle incoming MCP messages and pass them to the server via transport
    """

    def type(self):
        """Return the protocol type identifier."""
        return "MCP"

    @asynccontextmanager
    async def create_client(
        self,
        topic: str,
        url: str = None,
        transport: BaseTransport = None,
        message_timeout: int = 15,
        message_retries: int = 2,
        **kwargs,
    ) -> ClientSession:
        """
        Create and manage an MCP client session.

        This method establishes a complete MCP client session with bidirectional
        communication streams, handling message routing through the specified transport.

        Args:
            topic: The communication topic/channel identifier
            url: Optional URL for the MCP server (unused in current implementation)
            transport: The transport layer for message delivery
            message_timeout: Maximum time to wait for message responses
            message_retries: Number of retry attempts for failed messages
            **kwargs: Additional arguments passed to ClientSession

        Yields:
            ClientSession: An active MCP client session ready for communication

        Example:
            async with protocol.create_client("my-topic", transport=transport) as session:
                # Use session for MCP communication
                pass
        """
        if transport:
            await transport.setup()

        # Store timeout and retry configuration for this session
        self.message_timeout = message_timeout
        self.message_retries = message_retries

        # Check if distributed tracing is enabled (placeholder for future implementation)
        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            pass

        # Define the send method that will be used by the write stream
        # This method bridges the session messages to the transport layer
        async def send_method(session_message: SessionMessage):
            await self._client_send(transport, session_message, topic)

        # Create bidirectional streams and establish the MCP session
        async with self.new_streams(send_method) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, **kwargs
            ) as mcp_session:
                yield mcp_session

    def create_agent_topic(self):
        raise NotImplementedError(
            "MCPProtocol does not implement create_agent_topic yet."
        )

    @asynccontextmanager
    async def new_streams(self, send_method: Callable, **kwargs):
        """
        Create bidirectional memory streams for MCP message communication.

        This method establishes in-memory streams that bridge MCP session messages
        with the underlying transport layer. It creates two concurrent tasks:
        - Reader: Routes incoming messages from transport to the read stream
        - Writer: Routes outgoing messages from write stream to transport

        Args:
            send_method: Callable that handles sending messages via transport
            **kwargs: Additional configuration options (unused)

        Yields:
            tuple: (read_stream, write_stream) - Bidirectional communication streams

        Architecture:
            Transport -> Reader Task -> read_stream -> MCP Session
            MCP Session -> write_stream -> Writer Task -> Transport
        """
        # Initialize bidirectional memory streams for internal communication
        # read_stream: Messages flowing from transport to MCP session
        read_stream: MemoryObjectReceiveStream[SessionMessage | Exception]
        read_stream_writer: MemoryObjectSendStream[SessionMessage | Exception]
        # write_stream: Messages flowing from MCP session to transport
        write_stream: MemoryObjectSendStream[SessionMessage]
        write_stream_reader: MemoryObjectReceiveStream[SessionMessage]

        # Create unbuffered memory object streams (capacity=0 for direct flow)
        read_stream_writer, read_stream = anyio.create_memory_object_stream(0)
        write_stream, write_stream_reader = anyio.create_memory_object_stream(0)

        # Store writer reference for use in message handling
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
                        await send_method(session_message)
                    except Exception:
                        logger.error("Error sending message", exc_info=True)
                        raise
            finally:
                # Ensure write stream is properly closed
                await write_stream_reader.aclose()

        # Create concurrent task group to run reader and writer simultaneously
        async with anyio.create_task_group() as tg:
            # Start both reader and writer tasks concurrently
            tg.start_soon(reader)
            tg.start_soon(writer)

            try:
                # Yield the streams for use by the MCP session
                yield read_stream, write_stream
            finally:
                # Cleanup: Cancel all tasks and close streams
                tg.cancel_scope.cancel()
                logger.info("Closing MCP session streams.")
                await read_stream_writer.aclose()
                await write_stream.aclose()

    async def _client_send(self, transport, session_message, topic):
        """
        Send a session message through the transport layer and handle the response.

        Args:
            transport: The transport layer to send messages through
            session_message: The MCP session message to send
            topic: The communication topic/channel

        Raises:
            ValueError: If no response is received from the MCP server

        Message Flow:
            SessionMessage -> JSON-RPC -> Transport -> Server
            Server -> Transport -> JSON-RPC -> SessionMessage -> Session
        """
        # Serialize the MCP message to JSON-RPC format
        # model_dump with specific options ensures proper serialization
        msg_dict = session_message.message.model_dump(
            by_alias=True,  # Use field aliases for JSON compatibility
            mode="json",  # JSON serialization mode
            exclude_none=True,  # Omit None values from output
        )

        # Send message through transport and wait for response
        resp = await transport.publish(
            topic=topic,
            respond=True,  # Expect a response from the server
            message=Message(
                type=str(types.JSONRPCMessage),
                payload=json.dumps(msg_dict),
            ),
        )

        # Validate that we received a response
        if not resp:
            raise ValueError("No response received from MCP server")

        # Deserialize the response back to MCP format
        msg = resp.payload.decode()
        json_rpc_message = types.JSONRPCMessage.model_validate_json(msg)

        # Route the response back to the session via the read stream
        await self.read_stream_writer.send(SessionMessage(json_rpc_message))

    def bind_server(self, server: MCPServer | FastMCP) -> None:
        """
        Bind an MCP server instance to this protocol for handling incoming requests.
        """
        # Validate server type
        if not isinstance(server, (MCPServer, FastMCP)):
            raise ValueError("Server must be an instance of MCPServer or FastMCP")

        # Handle FastMCP wrapper by extracting the underlying server
        if isinstance(server, FastMCP):
            logger.warning(
                "FastMCP Server not natively supported, downgrading to its low-level server instance."
            )
            self._low_level_server = server._mcp_server
        else:
            self._low_level_server = server

    async def setup_ingress_handler(self) -> None:
        """
        Set up the ingress handler to process incoming MCP requests.

        This method establishes the server-side message processing pipeline:
        1. Creates a response tracking system for request-response correlation
        2. Sets up bidirectional streams for server communication
        3. Starts the MCP server with proper initialization
        4. Enables stateless operation for scalability

        The ingress handler processes incoming requests and routes responses
        back to the appropriate clients based on request IDs.

        Raises:
            ValueError: If no MCP server is bound to the protocol

        Architecture:
            Incoming Request -> Stream -> MCP Server -> Response -> Reply Method -> Client
        """
        # Ensure a server is bound before setting up ingress
        if not self._low_level_server:
            raise ValueError(
                "MCP server is not bound to the protocol, please bind it first"
            )

        # Initialize response tracking for request-response correlation
        # Maps request IDs to futures waiting for responses
        self._response_futures: dict[str, asyncio.Future] = {}

        async def reply_method(session_message: SessionMessage):
            """
            Handle outgoing responses from the MCP server.

            This method correlates server responses with pending client requests
            using request IDs, and fulfills the corresponding futures.

            Args:
                session_message: The response message from the MCP server
            """
            request_id = session_message.message.root.id
            if request_id in self._response_futures:
                # Fulfill the waiting future with the response
                self._response_futures[request_id].set_result(session_message)
            else:
                # Log unexpected responses (possible timing issues)
                logger.warning(f"Unexpected response for id={request_id}")

        # Set up server streams and start the MCP server
        async with self.new_streams(reply_method) as (read_stream, write_stream):
            await self._low_level_server.run(
                read_stream,
                write_stream,
                self._low_level_server.create_initialization_options(),
                stateless=True,  # Enable stateless operation for scalability
            )

            logger.info("[setup] MCP server started successfully.")

    async def handle_message(self, message: Message, timeout: int = 15) -> Message:
        """
        Handle an incoming MCP message and return the server's response.

        This method processes incoming messages by:
        1. Deserializing JSON-RPC messages to MCP format
        2. Creating response futures for request tracking
        3. Routing messages to the MCP server via streams
        4. Waiting for and returning the server's response

        Args:
            message: The incoming message to process

        Returns:
            Message: The server's response message in JSON-RPC format

        Raises:
            TimeoutError: If no response is received within the timeout period
            Exception: For other processing errors

        Message Flow:
            Incoming Message -> JSON-RPC Parse -> MCP Server -> Response -> JSON-RPC Format
        """
        # Deserialize the incoming JSON-RPC message
        rpc_message = types.JSONRPCMessage.model_validate_json(message.payload.decode())

        # Create a future to track the response for this request
        future = asyncio.get_event_loop().create_future()
        self._response_futures[rpc_message.root.id] = future

        # Route the message to the MCP server via the read stream
        session_message = SessionMessage(rpc_message)
        await self.read_stream_writer.send(session_message)

        try:
            # Wait for the server's response with a timeout
            response = await asyncio.wait_for(future, timeout=timeout)

            # Serialize the response back to JSON-RPC format
            return Message(
                type=str(types.JSONRPCMessage),
                payload=json.dumps(
                    response.message.model_dump(
                        by_alias=True,  # Use field aliases
                        mode="json",  # JSON serialization mode
                        exclude_none=True,  # Omit None values
                    )
                ),
            )
        except asyncio.TimeoutError:
            # Handle timeout - log and raise appropriate error
            logger.warning(f"Timeout waiting for response for id={rpc_message.root.id}")
            raise TimeoutError(
                f"Timeout waiting for response for id={rpc_message.root.id}"
            )
        except Exception as e:
            # Handle other errors with proper logging
            logger.error(f"Error waiting for response: {e}")
            raise e

    def message_translator(self, request: Any) -> Message:
        """
        Translate a request into a Message object.
        This method should be implemented to convert the request format
        into the Message format used by the MCP protocol.
        """
        pass
