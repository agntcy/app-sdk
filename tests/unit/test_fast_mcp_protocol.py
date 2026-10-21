# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

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


class _RejectingApp(Starlette):
    """Answers like the Identity Service middleware does on a rejection."""

    def __init__(self, status: int) -> None:
        super().__init__()
        self.status = status

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": self.status})
        await send(
            {
                "type": "http.response.body",
                "body": json.dumps(
                    {"error": "unauthorized", "reason": "policy detail"}
                ).encode(),
            }
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_auth_rejection_reply_carries_its_status(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    logger = MagicMock()
    monkeypatch.setattr("agntcy_app_sdk.semantic.fast_mcp.protocol.logger", logger)
    protocol = FastMCPProtocol()
    protocol._app = _RejectingApp(status)
    request = Message(
        type="MCPRequest",
        payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
    )

    response = await asyncio.wait_for(protocol.handle_message(request), 5)

    assert response.status_code == status
    assert "error" in json.loads(response.payload)
    assert b"policy detail" not in response.payload
    assert "policy detail" in str(logger.warning.call_args)


class _ToolResultApp(Starlette):
    """Answers 200 with a tool result that mentions auth words."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        result = {"content": [{"type": "text", "text": "User is unauthorized"}]}
        event = json.dumps({"jsonrpc": "2.0", "id": 1, "result": result})
        await send(
            {"type": "http.response.body", "body": f"data: {event}\n\n".encode()}
        )


@pytest.mark.asyncio
async def test_successful_result_mentioning_auth_words_is_not_an_error() -> None:
    protocol = FastMCPProtocol()
    protocol._app = _ToolResultApp()
    request = Message(
        type="MCPRequest",
        payload=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}),
    )

    response = await asyncio.wait_for(protocol.handle_message(request), 5)

    reply = json.loads(response.payload)
    assert "error" not in reply
    assert reply["result"]["content"][0]["text"] == "User is unauthorized"
    assert response.status_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "kept"),
    [
        ("tools/call", {"name": "weather"}, {None}),
        ("tools/list", None, {None, "weather"}),
    ],
)
async def test_rejected_tool_call_drops_that_tools_token(
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    params: dict[str, Any] | None,
    kept: set[str | None],
) -> None:
    from agntcy_app_sdk.common import auth

    monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
    cache = {
        ("unit-test-placeholder", None): ("agent-token", float("inf")),
        ("unit-test-placeholder", "weather"): ("tool-token", float("inf")),
    }
    monkeypatch.setattr(auth, "_token_cache", dict(cache))
    transport = AsyncMock(spec=BaseTransport)
    transport.request.return_value = Message(
        type="MCPResponse",
        payload=json.dumps({"error": "Authentication failed"}).encode(),
        status_code=403,
    )
    client = MCPClient(transport=transport, session_id="s", topic="mcp_t")

    with pytest.raises(RuntimeError, match="Authentication failed"):
        await client.call_mcp_method(method, params)

    assert {tool for _, tool in auth._token_cache} == kept


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


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [True, False])
async def test_setup_warns_that_token_checks_block_the_loop(
    monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    if enabled:
        monkeypatch.setenv("IDENTITY_AUTH_ENABLED", "true")
        monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
    else:
        monkeypatch.delenv("IDENTITY_AUTH_ENABLED", raising=False)
    logger = MagicMock()
    monkeypatch.setattr("agntcy_app_sdk.semantic.fast_mcp.protocol.logger", logger)
    uvicorn_server = MagicMock()
    uvicorn_server.return_value.serve = AsyncMock()
    monkeypatch.setattr("uvicorn.Server", uvicorn_server)
    protocol = FastMCPProtocol()
    protocol.bind_server(FastMCP("t"))

    await protocol.setup()

    warned = any(
        "blocking call on the event loop and no deadline" in c.args[0]
        and "nothing else on this event loop runs" in c.args[0]
        and "indefinitely" in c.args[0]
        for c in logger.warning.call_args_list
    )
    assert warned is enabled


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
