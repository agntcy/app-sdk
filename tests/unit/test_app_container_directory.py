# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for AppContainer directory lifecycle integration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.types import AgentCapabilities, AgentCard

from agntcy_app_sdk.app_sessions import AppContainer

pytest_plugins = "pytest_asyncio"


def _minimal_card() -> AgentCard:
    return AgentCard(
        name="dir-test-agent",
        url="http://localhost:9000",
        version="1.0.0",
        description="A test agent for directory tests",
        capabilities=AgentCapabilities(),
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        skills=[],
    )


def _make_handler(agent_record=None):
    """Create a mock ServerHandler."""
    handler = AsyncMock()
    handler.get_agent_record = MagicMock(return_value=agent_record)
    handler.topic = None
    handler.transport = None
    return handler


# ---------------------------------------------------------------------------
# Tests — run() lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_with_directory_pushes_record():
    """When a directory is configured and the handler provides a record, push it."""
    card = _minimal_card()
    handler = _make_handler(agent_record=card)
    directory = AsyncMock()
    directory.push_agent_record.return_value = "baeareifake123"

    container = AppContainer(handler, directory=directory)
    await container.run(keep_alive=False)

    handler.setup.assert_awaited_once()
    directory.setup.assert_awaited_once()
    handler.get_agent_record.assert_called_once()
    directory.push_agent_record.assert_awaited_once_with(card)
    assert container.is_running is True
    assert container.directory_cid == "baeareifake123"


@pytest.mark.asyncio
async def test_run_with_directory_no_record():
    """When the handler returns None, push should NOT be called."""
    handler = _make_handler(agent_record=None)
    directory = AsyncMock()

    container = AppContainer(handler, directory=directory)
    await container.run(keep_alive=False)

    handler.setup.assert_awaited_once()
    directory.setup.assert_awaited_once()
    handler.get_agent_record.assert_called_once()
    directory.push_agent_record.assert_not_awaited()
    assert container.directory_cid is None


@pytest.mark.asyncio
async def test_run_without_directory():
    """When no directory is configured, no directory calls should be made."""
    handler = _make_handler(agent_record=_minimal_card())

    container = AppContainer(handler, directory=None)
    await container.run(keep_alive=False)

    handler.setup.assert_awaited_once()
    # No directory calls — just verify no AttributeError
    assert container.is_running is True
    assert container.directory_cid is None


# ---------------------------------------------------------------------------
# Tests — stop() teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_calls_directory_teardown():
    """stop() calls directory.teardown() when a directory is configured."""
    handler = _make_handler()
    directory = AsyncMock()

    container = AppContainer(handler, directory=directory)
    container.is_running = True

    await container.stop()

    handler.teardown.assert_awaited_once()
    directory.teardown.assert_awaited_once()
    assert container.is_running is False


@pytest.mark.asyncio
async def test_stop_without_directory():
    """stop() succeeds without error when no directory is configured."""
    handler = _make_handler()

    container = AppContainer(handler, directory=None)
    container.is_running = True

    await container.stop()

    handler.teardown.assert_awaited_once()
    assert container.is_running is False
