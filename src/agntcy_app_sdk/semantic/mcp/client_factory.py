# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Optional

from agntcy_app_sdk.semantic.base import ClientFactory
from agntcy_app_sdk.semantic.mcp.protocol import MCPProtocol
from agntcy_app_sdk.transport.base import BaseTransport


class MCPClientFactory(ClientFactory):
    """Client factory for the MCP protocol. Returns an async context manager."""

    def protocol_type(self) -> str:
        return "MCP"

    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> Any:
        """Create an MCP client session (async context manager).

        Delegates to MCPProtocol.create_client() which returns an
        async context manager yielding a ClientSession.
        """
        protocol = MCPProtocol()
        return protocol.create_client(
            topic=topic, url=url, transport=transport, **kwargs
        )
