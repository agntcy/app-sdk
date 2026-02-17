# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, AsyncIterator, Optional

from mcp import ClientSession

from agntcy_app_sdk.semantic.mcp.protocol import MCPProtocol
from agntcy_app_sdk.transport.base import BaseTransport


class MCPClientFactory:
    """Client factory for the MCP protocol. Returns an async context manager."""

    ACCESSOR_NAME: str = "mcp"
    """Method name attached to :class:`AgntcyFactory` for this protocol."""

    def protocol_type(self) -> str:
        return "MCP"

    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ClientSession]:
        """Create an MCP client session (async context manager).

        Delegates to ``MCPProtocol.create_client()`` which is an
        ``@asynccontextmanager`` generator yielding a ``ClientSession``.

        Because the underlying generator is **not** a coroutine, there is no
        ``await`` here — we simply return the context manager object.  The
        ``async`` on this wrapper means callers use the two-step pattern::

            ctx = await factory.mcp().create_client(topic=..., transport=...)
            async with ctx as session:
                ...
        """
        protocol = MCPProtocol()
        return protocol.create_client(
            topic=topic, url=url, transport=transport, **kwargs
        )
