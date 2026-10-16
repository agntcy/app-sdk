# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, AsyncIterator, Awaitable, Callable
import json

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport

from mcp import ClientSession
import mcp.types as types
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.fastmcp import FastMCP
from mcp.shared.message import SessionMessage

from contextlib import asynccontextmanager
import asyncio
import anyio
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

logger = get_logger(__name__)


class MCPProtocol:
    """
    MCPProtocol implements the BaseAgentProtocol to bridge MCP client and server sessions
    with a generic transport layer.

    Key responsibilities:
    - Create and manage MCP client sessions with bidirectional streams
    - Handle incoming MCP messages and pass them to the server via transport
    """

    def __init__(self) -> None:
        self._low_level_server: MCPServer | None = None

    def type(self) -> str:
        """Return the protocol type identifier."""
        return "MCP"

    @asynccontextmanager
    async def create_client(
        self,
        topic: str,
        url: str | None = None,
        transport: BaseTransport | None = None,
        message_timeout: int = 15,
        message_retries: int = 2,
        **kwargs: Any,
    ) -> AsyncIterator[ClientSession]:
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

        # Define the send method that will be used by the write stream
        # This method bridges the session messages to the transport layer
        async def send_method(session_message: SessionMessage) -> None:
            await self._client_send(transport, session_message, topic)

        # Create bidirectional streams and establish the MCP session
        async with self.new_streams(send_method) as (read_stream, write_stream):
            async with ClientSession(
                read_stream, write_stream, **kwargs
            ) as mcp_session:
                yield mcp_session

    @asynccontextmanager
    async def new_streams(
        self,
        send_method: Callable[[SessionMessage], Awaitable[None]],
        **kwargs: Any,
    ) -> AsyncIterator[
        tuple[
            MemoryObjectReceiveStream[SessionMessage | Exception],
            MemoryObjectSendStream[SessionMessage],
        ]
    ]:
        """
        Create bidirectional memory streams for MCP message communication.

        This method establishes in-memory streams that bridge MCP session messages
        with the underlying transport layer. Incoming messages are pushed
        straight into the read stream via ``self.read_stream_writer``; a
        writer task routes outgoing messages from the write stream to the
        transport.

        Args:
            send_method: Callable that handles sending messages via transport
            **kwargs: Additional configuration options (unused)

        Yields:
            tuple: (read_stream, write_stream) - Bidirectional communication streams

        Architecture:
            Transport -> read_stream_writer -> read_stream -> MCP Session
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

        async def writer() -> None:
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

        async with anyio.create_task_group() as tg:
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

    async def _client_send(
        self,
        transport: BaseTransport | None,
        session_message: SessionMessage,
        topic: str,
    ) -> None:
        """
        Send a session message through the transport layer and handle the response.

        Args:
            transport: The transport layer to send messages through
            session_message: The MCP session message to send
            topic: The communication topic/channel

        Raises:
            ValueError: If no transport is given

        Message Flow:
            SessionMessage -> JSON-RPC -> Transport -> Server
            Server -> Transport -> JSON-RPC -> SessionMessage -> Session
        """
        root = session_message.message.root
        if not isinstance(root, types.JSONRPCRequest):
            # Only requests get a reply over the transport; a notification
            # sent through request() would block until it times out.
            logger.debug(f"Dropping client {type(root).__name__}: not a request")
            return

        # Serialize the MCP message to JSON-RPC format
        # model_dump with specific options ensures proper serialization
        msg_dict = session_message.message.model_dump(
            by_alias=True,  # Use field aliases for JSON compatibility
            mode="json",  # JSON serialization mode
            exclude_none=True,  # Omit None values from output
        )

        if transport is None:
            raise ValueError("Transport must be provided to send MCP messages")

        # Send message through transport and wait for response
        resp = await transport.request(
            recipient=topic,
            message=Message(
                type=str(types.JSONRPCMessage),
                payload=json.dumps(msg_dict),
            ),
        )

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

    async def setup(self, *args: Any, **kwargs: Any) -> None:
        if not self._low_level_server:
            raise ValueError(
                "MCP server is not bound to the protocol, please bind it first"
            )

        self._response_futures: dict[
            types.RequestId, asyncio.Future[SessionMessage]
        ] = {}

        async def reply_method(session_message: SessionMessage) -> None:
            root = session_message.message.root
            if not isinstance(root, (types.JSONRPCResponse, types.JSONRPCError)):
                # The transport can only answer a request; there is no
                # channel to push server notifications to the client.
                logger.debug(f"Dropping server {type(root).__name__}: not a reply")
                return
            request_id = root.id
            fut = self._response_futures.get(request_id)
            if fut and not fut.done():
                fut.set_result(session_message)
            elif fut:
                logger.debug(f"Ignoring response for id={request_id} (already done)")
            else:
                logger.warning(f"Unexpected response for id={request_id}")

        server = self._low_level_server

        async def _run_server() -> None:
            async with self.new_streams(reply_method) as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                    stateless=True,
                )
                logger.info("[setup] MCP server stopped.")

        # 🔹 Run the server in the background, non-blocking
        self._server_task = asyncio.create_task(_run_server())

        logger.info("[setup] MCP server started.")

    async def handle_message(self, message: Message, timeout: int = 90) -> Message:
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
        request = rpc_message.root
        if not isinstance(request, types.JSONRPCRequest):
            raise ValueError(
                f"Expected a JSON-RPC request, got {type(request).__name__}"
            )

        # Create a future to track the response for this request
        future = asyncio.get_event_loop().create_future()
        self._response_futures[request.id] = future

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
            logger.warning(f"Timeout waiting for response for id={request.id}")
            raise TimeoutError(f"Timeout waiting for response for id={request.id}")
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
        raise NotImplementedError("MCPProtocol does not translate requests")
