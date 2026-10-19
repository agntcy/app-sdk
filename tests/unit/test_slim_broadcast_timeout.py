# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A SLIM broadcast / group chat that runs out of time raises ``TimeoutError``.

The client turns that ``TimeoutError`` into ``A2ABroadcastTimeoutError``, so it
must not be hidden by an ``UnboundLocalError`` (timeout during session setup)
or turned into a ``CancelledError`` (timeout while the consumer is busy
between events).

A receive error on the group session must not spin until that timeout: a
closed session raises at once, other errors are retried a few times with a
pause, and an undecodable reply is skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import slim_bindings

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim import transport as slim_transport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport, _SlimSessionError

pytest_plugins = "pytest_asyncio"


def _transport(session_manager: MagicMock) -> SLIMTransport:
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


def _session_manager(
    session: MagicMock | None = None, *, hang: bool = False
) -> MagicMock:
    manager = MagicMock()
    manager.close_session = AsyncMock()

    async def open_session(
        *_args: Any, **_kwargs: Any
    ) -> tuple[None, MagicMock | None]:
        if hang:
            await asyncio.Event().wait()
        return None, session

    manager.group_broadcast_session = AsyncMock(side_effect=open_session)
    return manager


async def _consume_slowly(stream: AsyncIterator[Message], delay: float) -> None:
    async for _ in stream:
        await asyncio.sleep(delay)


class TestGatherStreamTimeout:
    @pytest.mark.asyncio
    async def test_timeout_during_session_setup_raises_timeout_error(self) -> None:
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
    async def test_slow_consumer_gets_timeout_error_not_cancelled(self) -> None:
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
    async def test_no_reply_raises_timeout_error_and_closes_the_session(self) -> None:
        session = _replying_session()

        async def never(**_kwargs: Any) -> None:
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
    async def test_timeout_during_session_setup_raises_timeout_error(self) -> None:
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
    async def test_slow_consumer_gets_timeout_error_not_cancelled(self) -> None:
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


def _reply(payload: bytes) -> SimpleNamespace:
    return SimpleNamespace(payload=Message(type="reply", payload=payload).serialize())


def _broadcast(transport: SLIMTransport) -> AsyncIterator[Message]:
    return transport.gather_stream(
        "org/ns/topic",
        Message(type="req", payload=b"hi"),
        recipients=["org/ns/a"],
        message_limit=1,
        timeout=5,
    )


def _groupchat(transport: SLIMTransport) -> AsyncIterator[Message]:
    return transport.start_streaming_conversation(
        "org/ns/chat",
        ["org/ns/a"],
        Message(type="req", payload=b"hi"),
        end_message="done",
        timeout=5,
    )


@pytest.fixture
def fast_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(slim_transport, "_RECEIVE_BACKOFF", 0.01)


@pytest.mark.parametrize("open_stream", [_broadcast, _groupchat])
class TestReceiveErrors:
    @pytest.mark.asyncio
    async def test_closed_session_raises_at_once(self, open_stream: Any) -> None:
        session = _replying_session()
        session.get_message_async = AsyncMock(
            side_effect=_SlimSessionError("session closed")
        )
        manager = _session_manager(session)

        start = asyncio.get_running_loop().time()
        with pytest.raises(_SlimSessionError, match="session closed"):
            await _consume_slowly(open_stream(_transport(manager)), delay=0)

        assert session.get_message_async.await_count == 1
        assert asyncio.get_running_loop().time() - start < 2
        manager.close_session.assert_awaited_once_with(session)

    @pytest.mark.asyncio
    async def test_repeated_errors_raise_after_retries(
        self, open_stream: Any, fast_backoff: None
    ) -> None:
        session = _replying_session()
        session.get_message_async = AsyncMock(side_effect=RuntimeError("boom"))
        manager = _session_manager(session)

        with pytest.raises(RuntimeError, match="boom"):
            await _consume_slowly(open_stream(_transport(manager)), delay=0)

        assert (
            session.get_message_async.await_count == slim_transport._RECEIVE_RETRIES + 1
        )
        manager.close_session.assert_awaited_once_with(session)

    @pytest.mark.asyncio
    async def test_transient_error_and_bad_payload_are_skipped(
        self, open_stream: Any, fast_backoff: None
    ) -> None:
        session = _replying_session()
        session.get_message_async = AsyncMock(
            side_effect=[
                RuntimeError("blip"),
                SimpleNamespace(payload=b"not a message"),
                _reply(b"done"),
            ]
        )
        manager = _session_manager(session)

        stream = open_stream(_transport(manager))
        replies = [m async for m in stream]

        assert [m.payload for m in replies] == [b"done"]
        assert session.get_message_async.await_count == 3


class TestRequestStreamErrors:
    @staticmethod
    def _stream(error: BaseException) -> AsyncIterator[Message]:
        session = MagicMock()
        session.publish_async = AsyncMock()
        session.session_id.return_value = 1
        session.get_message_async = AsyncMock(side_effect=error)
        manager = MagicMock()
        manager.close_session = AsyncMock()
        manager.point_to_point_session = AsyncMock(return_value=session)
        app = MagicMock()
        app.set_route_async = AsyncMock()
        transport = _transport(manager)
        transport._slim_app = app
        transport._slim_connection_id = 1
        return transport.request_stream(
            "org/ns/agent", Message(type="req", payload=b"")
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error", [asyncio.TimeoutError(), _SlimSessionError("session closed")]
    )
    async def test_failure_is_raised_not_hidden(self, error: BaseException) -> None:
        with pytest.raises(type(error)):
            _ = [m async for m in self._stream(error)]

    @pytest.mark.asyncio
    async def test_slim_receive_timeout_is_raised_as_timeout_error(self) -> None:
        error = getattr(slim_bindings.SlimError, "Timeout")()
        with pytest.raises(TimeoutError) as raised:
            _ = [m async for m in self._stream(error)]
        assert raised.value.__cause__ is error
