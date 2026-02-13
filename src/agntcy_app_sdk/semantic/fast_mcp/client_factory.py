# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Optional

from agntcy_app_sdk.semantic.base import ClientFactory
from agntcy_app_sdk.semantic.fast_mcp.protocol import FastMCPProtocol
from agntcy_app_sdk.transport.base import BaseTransport


class FastMCPClientFactory(ClientFactory):
    """Client factory for the FastMCP protocol."""

    def protocol_type(self) -> str:
        return "FastMCP"

    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> Any:
        """Create a FastMCP client. Delegates to FastMCPProtocol.create_client()."""
        protocol = FastMCPProtocol()
        return await protocol.create_client(
            url=url, topic=topic, transport=transport, **kwargs
        )
