# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0
import asyncio
from contextlib import AsyncExitStack

from mcp import ClientSession

from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.transports.streamable_http.models import StreamsContextProtocol
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.protocols.message import Message
from typing import Callable, Dict, Optional

configure_logging()
logger = get_logger(__name__)


class StreamableHTTPTransport(BaseTransport):
    def __init__(self, endpoint: str):
        self.endpoint: str = endpoint if endpoint else None
        self.session: Optional[ClientSession] = None
        self._exit_stack = AsyncExitStack()
        self._session_context: Optional[AsyncExitStack] = None
        self._streams_context: Optional[StreamsContextProtocol] = None

    @classmethod
    def from_client(cls, client):
        """Create a Streamable HTTP transport instance from a client."""
        return cls(client=client)

    @classmethod
    def from_config(cls, endpoint: str, **kwargs):
        """
        Create a Streamable HTTP transport instance from a configuration.
        :param endpoint: The Streamable HTTP server endpoint.
        :param kwargs: Additional configuration parameters.
        """
        return cls(endpoint=endpoint, **kwargs)

    def type(self) -> str:
        return "StreamableHTTPTransport"

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        raise NotImplementedError(
            "Set callback method is not implemented for Streamable HTTP transport"
        )

    async def connect(self, streams_context: StreamsContextProtocol):
        """Connect to the Streamable HTTP server."""
        if self.session is not None:
            logger.info("Already connected to Streamable HTTP server.")
            return
        self._streams_context = streams_context
        read_stream, write_stream, _ = await self._streams_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        self.session = await self._session_context.__aenter__()
        await self.session.initialize()
        logger.info(f"Connected to Streamable HTTP server at {self.endpoint}")

    # Duplicate method to maintain compatibility with MCP documentation
    async def cleanup(self) -> None:
        """Properly clean up the session and streams"""
        if self._session_context:
            await self._session_context.__aexit__(None, None, None)
        if self._streams_context:
            await self._streams_context.__aexit__(None, None, None)

    # Duplicate method to maintain compatibility with BaseTransport interface
    async def close(self) -> None:
        """Properly clean up the session and streams"""
        if self._session_context:
            await self._session_context.__aexit__(None, None, None)
        if self._streams_context:
            await self._streams_context.__aexit__(None, None, None)

    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish a message to a topic."""
        raise NotImplementedError(
            "Publish method is not implemented for Streamable HTTP transport"
        )

    async def subscribe(self, topic: str) -> None:
        raise NotImplementedError(
            "Subscribe method is not implemented for Streamable HTTP transport"
        )

    async def broadcast(
        self, topic, message, expected_responses=1, timeout=30, headers=None
    ):
        """Broadcast a message to all subscribers of a topic."""
        raise NotImplementedError(
            "Broadcast method is not implemented for Streamable HTTP transport"
        )
