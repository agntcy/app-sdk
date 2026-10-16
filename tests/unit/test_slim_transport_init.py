# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :class:`SLIMTransport` built on an injected SLIM app."""

import asyncio
import functools
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import slim_bindings

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.session_manager import SessionManager
from agntcy_app_sdk.transport.slim.transport import _SESSION_GONE, SLIMTransport

pytest_plugins = "pytest_asyncio"


def _transport() -> SLIMTransport:
    return SLIMTransport(
        routable_name="org/ns/agent",
        endpoint="http://localhost:46357",
        slim_app=MagicMock(),
        slim_connection_id=7,
    )


def _session_error(message: str) -> Exception:
    error: type[Exception] = getattr(slim_bindings.SlimError, "SessionError")
    return error(message)


def test_from_config_without_name_raises_value_error() -> None:
    with pytest.raises(ValueError, match="org/namespace/local_name"):
        SLIMTransport.from_config("http://localhost:46357")


def test_injected_app_without_connection_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="slim_connection_id is required"):
        SLIMTransport(
            routable_name="org/ns/agent",
            endpoint="http://localhost:46357",
            slim_app=MagicMock(),
        )


def test_injected_app_is_registered_with_the_session_manager() -> None:
    app = MagicMock()

    with patch.object(SessionManager, "set_slim") as set_slim:
        SLIMTransport(
            routable_name="org/ns/agent",
            endpoint="http://localhost:46357",
            slim_app=app,
            slim_connection_id=7,
        )

    set_slim.assert_called_once_with(app, 7)


@pytest.mark.asyncio
async def test_request_timeout_is_raised_and_session_closed() -> None:
    app = MagicMock()
    app.set_route_async = AsyncMock()
    session = MagicMock()
    session.publish_async = AsyncMock()
    session.get_message_async = AsyncMock(side_effect=asyncio.TimeoutError)
    transport = SLIMTransport(
        routable_name="org/ns/agent",
        endpoint="http://localhost:46357",
        slim_app=app,
        slim_connection_id=7,
    )

    with (
        patch.object(
            SessionManager, "point_to_point_session", AsyncMock(return_value=session)
        ),
        patch.object(SessionManager, "close_session", AsyncMock()) as close_session,
    ):
        with pytest.raises(TimeoutError):
            await transport.request(
                "org/ns/peer", Message(type="t", payload=b"hi"), timeout=1
            )

    close_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("text", _SESSION_GONE)
async def test_session_gone_error_stops_the_receive_loop(text: str) -> None:
    session = MagicMock()
    session.get_message_async = AsyncMock(
        side_effect=_session_error(f"SLIM error: {text.upper()}")
    )

    with patch.object(SessionManager, "close_session", AsyncMock()) as close_session:
        await asyncio.wait_for(_transport()._handle_session_receive(session), 5)

    session.get_message_async.assert_awaited_once()
    close_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_session_error_is_retried_then_session_closed() -> None:
    session = MagicMock()
    session.get_message_async = AsyncMock(side_effect=_session_error("boom"))

    with (
        patch.object(SessionManager, "close_session", AsyncMock()) as close_session,
        patch("agntcy_app_sdk.transport.slim.transport.asyncio.sleep", AsyncMock()),
    ):
        await asyncio.wait_for(_transport()._handle_session_receive(session), 5)

    assert session.get_message_async.await_count == 4
    close_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
@pytest.mark.parametrize("raises", [False, True])
async def test_callback_runs_once_with_publish_fn_only_when_accepted(
    raises: bool,
) -> None:
    calls: list[tuple[str, bool]] = []

    async def plain(message: Message) -> None:
        calls.append(("plain", False))
        if raises:
            raise TypeError("bug inside the handler")

    async def streaming(
        message: Message,
        *,
        publish_fn: Callable[[Message], Awaitable[None]] | None = None,
    ) -> None:
        calls.append(("streaming", publish_fn is not None))
        if raises:
            raise TypeError("bug inside the handler")

    transport = _transport()
    received = MagicMock(payload=Message(type="request", payload=b"ping").serialize())
    with patch.object(SLIMTransport, "_listen_for_sessions", AsyncMock()):
        for handler in (plain, streaming):
            transport.set_callback(handler)
            await transport._process_received_message(MagicMock(), received)

    assert calls == [("plain", False), ("streaming", True)]


@pytest.mark.asyncio
async def test_wraps_wrapper_is_called_once_without_publish_fn() -> None:
    calls: list[Message] = []

    async def streaming(
        message: Message,
        *,
        publish_fn: Callable[[Message], Awaitable[None]] | None = None,
    ) -> None:
        return None

    @functools.wraps(streaming)
    async def wrapper(message: Message) -> None:
        calls.append(message)

    transport = _transport()
    received = MagicMock(payload=Message(type="request", payload=b"ping").serialize())
    with patch.object(SLIMTransport, "_listen_for_sessions", AsyncMock()):
        transport.set_callback(wrapper)
        await transport._process_received_message(MagicMock(), received)

    assert len(calls) == 1
