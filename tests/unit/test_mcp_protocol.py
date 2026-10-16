# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import mcp.types as types
import pytest
import pytest_asyncio
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.session import ServerSession

from agntcy_app_sdk.semantic.mcp.client_factory import MCPClientFactory
from agntcy_app_sdk.semantic.mcp.handler import MCPServerHandler
from agntcy_app_sdk.semantic.mcp.protocol import MCPProtocol
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport


@pytest_asyncio.fixture
async def server() -> AsyncIterator[MCPProtocol]:
    mcp_server = FastMCP("test")

    @mcp_server.tool()
    async def ping(ctx: Context[ServerSession, object]) -> str:
        await ctx.info("server-side log notification")
        return "pong"

    protocol = MCPProtocol()
    protocol.bind_server(mcp_server)
    await protocol.setup()
    await asyncio.sleep(0)
    yield protocol
    protocol._server_task.cancel()


def _transport_to(server: MCPProtocol) -> AsyncMock:
    async def request(recipient: str, message: Message) -> Message:
        return await server.handle_message(message, timeout=5)

    transport = AsyncMock(spec=BaseTransport)
    transport.request.side_effect = request
    return transport


@pytest.mark.asyncio
async def test_server_notification_does_not_stop_the_server(
    server: MCPProtocol,
) -> None:
    client = MCPProtocol()
    async with client.create_client("t", transport=_transport_to(server)) as session:
        for _ in range(2):
            result = await asyncio.wait_for(session.call_tool("ping", {}), 5)
            assert isinstance(result.content[0], types.TextContent)
            assert result.content[0].text == "pong"

    assert not server._server_task.done()


@pytest.mark.asyncio
async def test_initialize_then_call_tool(server: MCPProtocol) -> None:
    transport = _transport_to(server)
    client = MCPProtocol()
    async with client.create_client("t", transport=transport) as session:
        await asyncio.wait_for(session.initialize(), 5)
        result = await asyncio.wait_for(session.call_tool("ping", {}), 5)

    assert not result.isError
    sent_methods = [
        json.loads(call.kwargs["message"].payload)["method"]
        for call in transport.request.call_args_list
    ]
    assert "notifications/initialized" not in sent_methods


@pytest.mark.asyncio
async def test_handle_message_rejects_notification(server: MCPProtocol) -> None:
    notification = Message(
        type=str(types.JSONRPCMessage),
        payload=json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
    )
    with pytest.raises(ValueError, match="got JSONRPCNotification"):
        await server.handle_message(notification)


@pytest.mark.asyncio
@pytest.mark.parametrize("topic", [None, ""])
async def test_client_factory_requires_topic(topic: str | None) -> None:
    with pytest.raises(ValueError, match="needs a topic"):
        await MCPClientFactory().create_client(
            topic=topic, transport=AsyncMock(spec=BaseTransport)
        )


def test_server_handler_requires_topic() -> None:
    with pytest.raises(ValueError, match="Topic must be provided"):
        MCPServerHandler(
            FastMCP("t")._mcp_server, transport=AsyncMock(spec=BaseTransport)
        )


@pytest.mark.asyncio
async def test_setup_before_bind_raises_value_error() -> None:
    with pytest.raises(ValueError, match="not bound"):
        await MCPProtocol().setup()


def test_message_translator_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        MCPProtocol().message_translator({})
