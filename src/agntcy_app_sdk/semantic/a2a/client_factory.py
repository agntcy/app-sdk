# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Any, Optional

from a2a.client import A2AClient

from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from agntcy_app_sdk.semantic.base import ClientFactory
from agntcy_app_sdk.transport.base import BaseTransport


class A2AClientFactory(ClientFactory):
    """Client factory for the A2A protocol."""

    def protocol_type(self) -> str:
        return "A2A"

    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> A2AClient:
        """Create an A2A client. Delegates to A2AProtocol.create_client()."""
        protocol = A2AProtocol()
        return await protocol.create_client(
            url=url, topic=topic, transport=transport, **kwargs
        )
