# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.types import Receive, Scope, Send

from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.semantic.fast_mcp.client import MCPClient
from agntcy_app_sdk.semantic.fast_mcp.handler import FastMCPServerHandler
from agntcy_app_sdk.semantic.fast_mcp.protocol import FastMCPProtocol
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport


class _StreamingApp(Starlette):
    """Answers with one SSE event, then listens for a disconnect like Starlette's
    StreamingResponse does."""

    def __init__(self) -> None:
        super().__init__()
        self.received: list[dict[str, Any]] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.received.append(dict(await receive()))
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send(
            {
                "type": "http.response.body",
                "body": b'data: {"jsonrpc": "2.0", "id": 1, "result": {}}\n\n',
                "more_body": False,
            }
        )
        self.received.append(dict(await asyncio.wait_for(receive(), 1)))


@pytest.mark.asyncio
async def test_receive_reports_disconnect_after_the_response() -> None:
    protocol = FastMCPProtocol()
    app = _StreamingApp()
    protocol._app = app
    request = Message(
        type="MCPRequest",
        payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    )

    response = await asyncio.wait_for(protocol.handle_message(request), 5)

    assert json.loads(response.payload)["id"] == 1
    assert [m["type"] for m in app.received] == ["http.request", "http.disconnect"]


@pytest.mark.asyncio
async def test_create_client_with_transport_requires_topic() -> None:
    transport = AsyncMock(spec=BaseTransport)

    with pytest.raises(ValueError, match="topic is required"):
        await FastMCPProtocol().create_client(
            url="http://localhost", transport=transport
        )

    transport.setup.assert_not_awaited()


@pytest.mark.asyncio
async def test_setup_before_bind_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not bound"):
        await FastMCPProtocol().setup()


def test_server_handler_with_transport_requires_topic() -> None:
    with pytest.raises(ValueError, match="Topic must be provided"):
        FastMCPServerHandler(FastMCP("t"), transport=AsyncMock(spec=BaseTransport))


@pytest.mark.asyncio
async def test_call_without_topic_raises_clear_error() -> None:
    transport = AsyncMock(spec=BaseTransport)
    client = MCPClient(transport=transport, session_id="s", topic=None)

    with pytest.raises(RuntimeError, match="Topic must be provided"):
        await client.list_tools()

    transport.request.assert_not_awaited()
