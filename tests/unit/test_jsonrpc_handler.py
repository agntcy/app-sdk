# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``A2AJsonRpcServerHandler`` bind-host decoupling."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from agntcy_app_sdk.semantic.a2a.server.jsonrpc import (
    A2AJsonRpcServerHandler,
    resolve_http_bind_host,
)


class TestResolveHttpBindHost:
    def test_default_all_interfaces(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGNTCY_A2A_HTTP_BIND_HOST", None)
            assert resolve_http_bind_host() == "0.0.0.0"

    def test_env_var(self):
        with patch.dict(
            os.environ, {"AGNTCY_A2A_HTTP_BIND_HOST": "127.0.0.1"}, clear=False
        ):
            assert resolve_http_bind_host() == "127.0.0.1"

    def test_explicit_beats_env(self):
        with patch.dict(
            os.environ, {"AGNTCY_A2A_HTTP_BIND_HOST": "127.0.0.1"}, clear=False
        ):
            assert resolve_http_bind_host("10.0.0.1") == "10.0.0.1"

    def test_explicit_beats_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGNTCY_A2A_HTTP_BIND_HOST", None)
            assert resolve_http_bind_host("127.0.0.1") == "127.0.0.1"


def _make_server() -> MagicMock:
    """Return a mock A2AStarletteApplication with a stub agent card."""
    server = MagicMock()
    server.agent_card = MagicMock()
    return server


class TestBindHost:
    def test_defaults_bind_to_advertised_host(self):
        handler = A2AJsonRpcServerHandler(_make_server(), host="example.com", port=9999)
        assert handler._host == "example.com"
        assert handler._bind_host == "example.com"
        assert handler._port == 9999

    def test_explicit_bind_host_overrides(self):
        handler = A2AJsonRpcServerHandler(
            _make_server(),
            host="example.com",
            port=9999,
            bind_host="127.0.0.1",
        )
        # Advertised host unchanged; only the bind interface differs.
        assert handler._host == "example.com"
        assert handler._bind_host == "127.0.0.1"

    def test_none_bind_host_falls_back_to_host(self):
        handler = A2AJsonRpcServerHandler(
            _make_server(),
            host="0.0.0.0",
            port=9999,
            bind_host=None,
        )
        assert handler._bind_host == "0.0.0.0"

    def test_agent_card_reports_advertised_host(self):
        server = _make_server()
        handler = A2AJsonRpcServerHandler(
            server, host="example.com", port=9999, bind_host="0.0.0.0"
        )
        assert handler.agent_card is server.agent_card


@pytest.mark.asyncio
async def test_setup_binds_bind_host(monkeypatch):
    """``setup()`` must configure uvicorn with the bind host, not the advertised host."""
    captured = {}

    class _FakeConfig:
        def __init__(self, *, app, host, port, loop):
            captured["host"] = host
            captured["port"] = port

    class _FakeServer:
        def __init__(self, config):
            self.should_exit = False

        async def serve(self):
            return None

    monkeypatch.setattr(
        "agntcy_app_sdk.semantic.a2a.server.jsonrpc.uvicorn.Config", _FakeConfig
    )
    monkeypatch.setattr(
        "agntcy_app_sdk.semantic.a2a.server.jsonrpc.uvicorn.Server", _FakeServer
    )

    server = _make_server()
    server.agent_card.preferred_transport = "JSONRPC"
    server.build.return_value = MagicMock()

    handler = A2AJsonRpcServerHandler(
        server, host="example.com", port=9999, bind_host="0.0.0.0"
    )
    await handler.setup()
    await handler.teardown()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 9999
