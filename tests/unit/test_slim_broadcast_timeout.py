# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A SLIM broadcast / group chat that runs out of time raises ``TimeoutError``.

The client turns that ``TimeoutError`` into ``A2ABroadcastTimeoutError``, so it
must not be hidden by an ``UnboundLocalError`` (timeout during session setup)
or turned into a ``CancelledError`` (timeout while the consumer is busy
between events).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

pytest_plugins = "pytest_asyncio"


def _transport(session_manager) -> SLIMTransport:
    transport = SLIMTransport(
        routable_name="org/ns/client", endpoint="http://localhost:46357"
    )
    transport._slim_app = MagicMock()
    transport._session_manager = session_manager
    return transport


def _replying_session() -> MagicMock:
    """A group session that answers every receive immediately."""
    session = MagicMock()
    session.publish_async = AsyncMock()
    session.session_id.return_value = 1
    reply = Message(type="reply", payload=b"hello").serialize()
    session.get_message_async = AsyncMock(return_value=SimpleNamespace(payload=reply))
    return session


def _session_manager(session: MagicMock | None = None, *, hang: bool = False):
    manager = MagicMock()
    manager.close_session = AsyncMock()

    async def open_session(*_args, **_kwargs):
        if hang:
            await asyncio.Event().wait()
        return None, session

    manager.group_broadcast_session = AsyncMock(side_effect=open_session)
    return manager


async def _consume_slowly(stream, delay: float) -> None:
    async for _ in stream:
        await asyncio.sleep(delay)


class TestGatherStreamTimeout:
    @pytest.mark.asyncio
    async def test_timeout_during_session_setup_raises_timeout_error(self):
        """An offline recipient stalls the invite; that is a timeout, not an
        ``UnboundLocalError`` from the cleanup code."""
        manager = _session_manager(hang=True)
        transport = _transport(manager)

        with pytest.raises(TimeoutError):
            await transport.gather(
                "org/ns/topic",
                Message(type="req", payload=b"hi"),
                recipients=["org/ns/agent"],
                timeout=0.2,
            )

        manager.close_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_slow_consumer_gets_timeout_error_not_cancelled(self):
        session = _replying_session()
        manager = _session_manager(session)
        transport = _transport(manager)

        stream = transport.gather_stream(
            "org/ns/topic",
            Message(type="req", payload=b"hi"),
            recipients=["org/ns/a", "org/ns/b", "org/ns/c"],
            message_limit=3,
            timeout=0.8,
        )
        with pytest.raises(TimeoutError):
            await _consume_slowly(stream, delay=0.4)

        manager.close_session.assert_awaited_once_with(session)

    @pytest.mark.asyncio
    async def test_no_reply_raises_timeout_error_and_closes_the_session(self):
        session = _replying_session()

        async def never(**_kwargs):
            await asyncio.Event().wait()

        session.get_message_async = AsyncMock(side_effect=never)
        manager = _session_manager(session)
        transport = _transport(manager)

        with pytest.raises(TimeoutError):
            await transport.gather(
                "org/ns/topic",
                Message(type="req", payload=b"hi"),
                recipients=["org/ns/agent"],
                timeout=0.7,
            )

        manager.close_session.assert_awaited_once_with(session)


class TestStreamingConversationTimeout:
    @pytest.mark.asyncio
    async def test_timeout_during_session_setup_raises_timeout_error(self):
        manager = _session_manager(hang=True)
        transport = _transport(manager)

        stream = transport.start_streaming_conversation(
            "org/ns/chat",
            ["org/ns/agent"],
            Message(type="req", payload=b"hi"),
            timeout=0.2,
        )
        with pytest.raises(TimeoutError):
            await _consume_slowly(stream, delay=0)

        manager.close_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_slow_consumer_gets_timeout_error_not_cancelled(self):
        session = _replying_session()
        manager = _session_manager(session)
        transport = _transport(manager)

        stream = transport.start_streaming_conversation(
            "org/ns/chat",
            ["org/ns/agent"],
            Message(type="req", payload=b"hi"),
            end_message="work-done",
            timeout=0.8,
        )
        with pytest.raises(TimeoutError):
            await _consume_slowly(stream, delay=0.4)

        manager.close_session.assert_awaited_once_with(session)
