# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from tests.server.a2a_starlette_server import default_a2a_server
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_app_session():
    """
    Unit test for the AgntcyFactory and its components using the fluent API.
    """

    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=1)

    # Create an app container via the fluent builder.
    # When no transport is provided for an A2AStarletteApplication, the SDK
    # falls back to the JSONRPC handler (native HTTP), so topic is not used.
    app_session.add(default_a2a_server).with_host("0.0.0.0").with_port(
        9000
    ).with_session_id("test_session").build()

    retrieved_container = app_session.get_app_container("test_session")

    assert retrieved_container is not None, "Failed to retrieve the app container."

    # test adding > max_sessions
    try:
        app_session.add(default_a2a_server).with_session_id(
            "invalid_test_session"
        ).build()
        assert False, "Max sessions should have been reached"
    except Exception:
        pass

    # test removing app container
    app_session.remove_app_container("test_session")
    assert app_session.get_app_container("test_session") is None, (
        "App container was not removed properly."
    )


@pytest.mark.asyncio
async def test_container_builder_passes_bind_host():
    """`.with_bind_host()` is forwarded to the JSONRPC handler, decoupled from host."""
    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=1)

    container = (
        app_session.add(default_a2a_server)
        .with_host("example.com")
        .with_bind_host("0.0.0.0")
        .with_port(9000)
        .with_session_id("bind_host_session")
        .build()
    )

    handler = container.handler
    assert handler._host == "example.com"
    assert handler._bind_host == "0.0.0.0"
    assert handler._port == 9000


@pytest.mark.asyncio
async def test_container_builder_bind_host_defaults_to_host():
    """Without `.with_bind_host()`, the handler binds the advertised host."""
    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=1)

    container = (
        app_session.add(default_a2a_server)
        .with_host("127.0.0.1")
        .with_port(9001)
        .with_session_id("default_bind_session")
        .build()
    )

    handler = container.handler
    assert handler._host == "127.0.0.1"
    assert handler._bind_host == "127.0.0.1"
