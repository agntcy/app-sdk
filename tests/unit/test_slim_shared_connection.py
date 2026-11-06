# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Every SLIM transport in a process shares one connection, by reference count.

``close()`` gives back only its own share.  The last one to close disconnects
and clears the cache, so a short-lived transport (a health probe, say) cannot
cut the connection under transports that still use it.
"""

import asyncio
import itertools
from collections.abc import Awaitable, Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agntcy_app_sdk.transport.slim import common
from agntcy_app_sdk.transport.slim import transport as slim_transport
from agntcy_app_sdk.transport.slim.session_manager import SessionManager
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

pytest_plugins = "pytest_asyncio"

_ENDPOINT = "http://slim.example:46357"
_SECRET = "unit-test-secret-0123456789abcdef"


@pytest.fixture
def service(monkeypatch: pytest.MonkeyPatch) -> Iterator[MagicMock]:
    """A fake SLIM service that hands out a new connection id per connect."""
    ids = itertools.count(100)
    service = MagicMock()
    service.connect_async = AsyncMock(side_effect=lambda _cfg: next(ids))
    service.create_app.side_effect = lambda *_a: MagicMock(subscribe_async=AsyncMock())
    for name, value in (
        ("global_slim", None),
        ("global_slim_service", None),
        ("global_connection_id", None),
        ("global_slim_insecure", None),
        ("global_slim_endpoint", None),
        ("global_slim_auth", None),
        ("global_slim_users", 0),
    ):
        monkeypatch.setattr(common, name, value)
    monkeypatch.setattr(common, "_retired_connection_users", {})
    monkeypatch.setattr(common, "get_or_init_slim_service", lambda: service)
    monkeypatch.setattr(common, "shared_secret_identity", lambda **_kw: (None, None))
    monkeypatch.setattr(common, "new_client_config", lambda *_a, **_kw: "cfg")
    yield service


async def _acquire() -> int:
    _, _, connection_id = await common.get_or_create_slim_instance(
        MagicMock(), _ENDPOINT, True, shared_secret=_SECRET
    )
    return connection_id


def _transport(name: str = "agent") -> SLIMTransport:
    return SLIMTransport(
        routable_name=f"org/ns/{name}",
        endpoint=_ENDPOINT,
        shared_secret_identity=_SECRET,
    )


class TestShareCounting:
    @pytest.mark.asyncio
    async def test_only_the_last_release_disconnects(self, service: MagicMock) -> None:
        first = await _acquire()
        second = await _acquire()

        assert first == second
        assert common.release_slim_connection(first) is False
        assert common.global_connection_id == first, "still cached for the others"
        assert common.release_slim_connection(second) is True

    @pytest.mark.asyncio
    async def test_last_release_clears_the_cache(self, service: MagicMock) -> None:
        connection_id = await _acquire()
        common.release_slim_connection(connection_id)

        assert common.global_slim is None
        assert common.global_slim_service is None
        assert common.global_connection_id is None
        assert common.global_slim_auth is None

    @pytest.mark.asyncio
    async def test_next_acquire_after_the_last_release_reconnects(
        self, service: MagicMock
    ) -> None:
        first = await _acquire()
        common.release_slim_connection(first)

        second = await _acquire()

        assert second != first
        assert service.connect_async.await_count == 2

    @pytest.mark.asyncio
    async def test_a_refused_request_takes_no_share(self, service: MagicMock) -> None:
        connection_id = await _acquire()
        with pytest.raises(ValueError, match="already open"):
            await common.get_or_create_slim_instance(
                MagicMock(), "http://elsewhere:1", True, shared_secret=_SECRET
            )

        assert common.release_slim_connection(connection_id) is True

    def test_an_unknown_connection_is_the_callers_to_disconnect(self) -> None:
        assert common.release_slim_connection(4242) is True


class TestRetire:
    @pytest.mark.asyncio
    async def test_next_acquire_opens_a_new_connection(
        self, service: MagicMock
    ) -> None:
        broken = await _acquire()

        assert common.retire_slim_connection(broken) is True
        replacement = await _acquire()

        assert replacement != broken

    @pytest.mark.asyncio
    async def test_holders_of_the_retired_connection_keep_it_until_the_last(
        self, service: MagicMock
    ) -> None:
        broken = await _acquire()
        await _acquire()
        common.retire_slim_connection(broken)

        assert common.release_slim_connection(broken) is False
        assert common.release_slim_connection(broken) is True

    @pytest.mark.asyncio
    async def test_retiring_does_not_touch_the_replacement(
        self, service: MagicMock
    ) -> None:
        broken = await _acquire()
        common.retire_slim_connection(broken)
        replacement = await _acquire()

        assert common.release_slim_connection(broken) is True
        assert common.global_connection_id == replacement
        assert common.release_slim_connection(replacement) is True

    @pytest.mark.asyncio
    async def test_a_late_report_cannot_retire_the_replacement(
        self, service: MagicMock
    ) -> None:
        broken = await _acquire()
        common.retire_slim_connection(broken)
        replacement = await _acquire()

        assert common.retire_slim_connection(broken) is False
        assert common.global_connection_id == replacement

    def test_nothing_cached_nothing_to_retire(self) -> None:
        assert common.retire_slim_connection() is False

    @pytest.mark.asyncio
    async def test_without_an_id_the_current_connection_is_retired(
        self, service: MagicMock
    ) -> None:
        await _acquire()

        assert common.retire_slim_connection() is True
        assert common.global_connection_id is None


class TestTransportClose:
    @pytest.mark.asyncio
    async def test_a_probe_closing_does_not_cut_the_others(
        self, service: MagicMock
    ) -> None:
        main, probe = _transport("main"), _transport("probe")
        await main.setup()
        await probe.setup()

        await probe.close()

        service.disconnect.assert_not_called()
        assert common.global_connection_id == main._slim_connection_id

        # A transport created after the probe closed gets the live connection,
        # not a dead id.
        later = _transport("later")
        await later.setup()
        assert later._slim_connection_id == main._slim_connection_id

    @pytest.mark.asyncio
    async def test_the_last_close_disconnects_once_and_clears_the_cache(
        self, service: MagicMock
    ) -> None:
        first, second = _transport("first"), _transport("second")
        await first.setup()
        await second.setup()
        connection_id = first._slim_connection_id

        await first.close()
        await second.close()

        service.disconnect.assert_called_once_with(connection_id)
        assert common.global_connection_id is None

    @pytest.mark.asyncio
    async def test_a_new_transport_after_the_last_close_gets_a_new_connection(
        self, service: MagicMock
    ) -> None:
        first = _transport("first")
        await first.setup()
        old_id = first._slim_connection_id
        await first.close()

        second = _transport("second")
        await second.setup()

        assert second._slim_connection_id != old_id

    @pytest.mark.asyncio
    async def test_closing_twice_gives_back_one_share(self, service: MagicMock) -> None:
        main, probe = _transport("main"), _transport("probe")
        await main.setup()
        await probe.setup()

        await probe.close()
        await probe.close()

        service.disconnect.assert_not_called()
        assert common.global_connection_id == main._slim_connection_id

    @pytest.mark.asyncio
    async def test_a_failed_disconnect_still_clears_the_cache(
        self, service: MagicMock
    ) -> None:
        service.disconnect.side_effect = RuntimeError("boom")
        only = _transport()
        await only.setup()

        await only.close()

        assert common.global_connection_id is None

    @pytest.mark.asyncio
    async def test_an_injected_connection_is_disconnected_by_its_transport(
        self, service: MagicMock
    ) -> None:
        shared = _transport("shared")
        await shared.setup()
        injected = SLIMTransport(
            routable_name="org/ns/injected",
            endpoint=_ENDPOINT,
            slim_service=service,
            slim_app=MagicMock(),
            slim_connection_id=7,
        )

        await injected.close()

        service.disconnect.assert_called_once_with(7)
        assert common.global_connection_id == shared._slim_connection_id


class TestMarkConnectionBroken:
    @pytest.mark.asyncio
    async def test_new_transports_get_a_new_connection(
        self, service: MagicMock
    ) -> None:
        victim = _transport("victim")
        await victim.setup()
        broken_id = victim._slim_connection_id

        victim.mark_connection_broken()
        fresh = _transport("fresh")
        await fresh.setup()

        assert fresh._slim_connection_id != broken_id

    @pytest.mark.asyncio
    async def test_the_broken_connection_stays_until_its_last_holder_closes(
        self, service: MagicMock
    ) -> None:
        reporter, bystander = _transport("reporter"), _transport("bystander")
        await reporter.setup()
        await bystander.setup()
        broken_id = reporter._slim_connection_id
        reporter.mark_connection_broken()
        fresh = _transport("fresh")
        await fresh.setup()

        await reporter.close()
        service.disconnect.assert_not_called()
        await bystander.close()

        service.disconnect.assert_called_once_with(broken_id)
        assert common.global_connection_id == fresh._slim_connection_id

    @pytest.mark.asyncio
    async def test_the_last_holder_reporting_and_closing_disconnects(
        self, service: MagicMock
    ) -> None:
        only = _transport()
        await only.setup()
        broken_id = only._slim_connection_id

        only.mark_connection_broken()
        await only.close()

        service.disconnect.assert_called_once_with(broken_id)

    @pytest.mark.asyncio
    async def test_a_late_report_does_not_retire_the_replacement(
        self, service: MagicMock
    ) -> None:
        old = _transport("old")
        await old.setup()
        old.mark_connection_broken()
        fresh = _transport("fresh")
        await fresh.setup()

        old.mark_connection_broken()

        assert common.global_connection_id == fresh._slim_connection_id

    @pytest.mark.asyncio
    async def test_it_is_a_no_op_once_closed(self, service: MagicMock) -> None:
        main, probe = _transport("main"), _transport("probe")
        await main.setup()
        await probe.setup()
        await probe.close()

        probe.mark_connection_broken()

        assert common.global_connection_id == main._slim_connection_id

    def test_it_is_a_no_op_for_an_injected_connection(self) -> None:
        injected = SLIMTransport(
            routable_name="org/ns/injected",
            endpoint=_ENDPOINT,
            slim_app=MagicMock(),
            slim_connection_id=7,
        )

        injected.mark_connection_broken()


async def _noop(_message: Any) -> None:
    return None


async def _listening(
    *names: str,
) -> tuple[list[SLIMTransport], "asyncio.Queue[MagicMock]"]:
    """Transports that listen on one shared app with a single session queue.

    Like the real shared ``App``, each ``listen_for_session_async`` call takes
    the next session, whoever asks.
    """
    queue: asyncio.Queue[MagicMock] = asyncio.Queue()
    transports = [_transport(name) for name in names]
    for transport in transports:
        await transport.setup()

    async def listen(timeout: object = None) -> MagicMock:
        return await queue.get()

    assert common.global_slim is not None
    setattr(common.global_slim, "listen_for_session_async", listen)
    for transport in transports:
        transport.set_callback(_noop)
    await asyncio.sleep(0)  # let every listener start waiting
    return transports, queue


def _on_session(
    transport: SLIMTransport, handler: Callable[[MagicMock], Awaitable[None]]
) -> None:
    """Replace what the transport's listener runs for each incoming session."""
    setattr(transport, "_handle_session_receive", handler)


def _record_sessions(transport: SLIMTransport) -> list[MagicMock]:
    pulled: list[MagicMock] = []

    async def handle(session: MagicMock) -> None:
        pulled.append(session)

    _on_session(transport, handle)
    return pulled


class TestCloseStopsTheInstance:
    """``close()`` stops what the transport started, whoever else is still open."""

    @pytest.mark.asyncio
    async def test_a_closed_transport_stops_taking_sessions_from_the_shared_app(
        self, service: MagicMock
    ) -> None:
        (closed, live), queue = await _listening("closed", "live")
        taken_by_closed = _record_sessions(closed)
        taken_by_live = _record_sessions(live)

        await closed.close()
        assert closed._listener_task is None
        for _ in range(6):
            queue.put_nowait(MagicMock())
            await asyncio.sleep(0.01)

        assert taken_by_closed == []
        assert len(taken_by_live) == 6
        service.disconnect.assert_not_called()
        await live.close()

    @pytest.mark.asyncio
    async def test_the_last_close_stops_its_listener_too(
        self, service: MagicMock
    ) -> None:
        (only,), _ = await _listening("only")
        listener = only._listener_task
        assert listener is not None

        await only.close()

        assert listener.done()
        service.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_clears_the_callback_and_signals_shutdown(
        self, service: MagicMock
    ) -> None:
        (closed, live), _ = await _listening("closed", "live")

        await closed.close()

        assert closed._callback is None
        assert closed._shutdown_event.is_set()
        assert live._callback is not None
        assert not live._shutdown_event.is_set()
        await live.close()

    @pytest.mark.asyncio
    async def test_running_session_handlers_are_cancelled(
        self, service: MagicMock
    ) -> None:
        (closed, live), queue = await _listening("closed", "live")
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def slow_handler(_session: MagicMock) -> None:
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        _on_session(closed, slow_handler)
        _record_sessions(live)
        queue.put_nowait(MagicMock())
        await asyncio.wait_for(started.wait(), 1)

        await closed.close()

        assert cancelled.is_set()
        assert not closed._tasks or all(t.done() for t in closed._tasks)
        await live.close()

    @pytest.mark.asyncio
    async def test_close_from_inside_a_handler_does_not_cancel_itself(
        self, service: MagicMock
    ) -> None:
        (transport,), queue = await _listening("only")
        finished = asyncio.Event()

        async def closing_handler(_session: MagicMock) -> None:
            await transport.close()
            finished.set()

        _on_session(transport, closing_handler)
        queue.put_nowait(MagicMock())

        await asyncio.wait_for(finished.wait(), 1)

        service.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_closing_twice_is_harmless(self, service: MagicMock) -> None:
        (closed, live), _ = await _listening("closed", "live")

        await closed.close()
        await closed.close()

        service.disconnect.assert_not_called()
        assert common.global_connection_id == live._slim_connection_id
        await live.close()
        service.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_an_injected_app_without_a_service_still_stops_its_tasks(
        self, service: MagicMock
    ) -> None:
        app = MagicMock()
        listening = asyncio.Event()

        async def listen(timeout: object = None) -> MagicMock:
            listening.set()
            await asyncio.sleep(60)
            raise AssertionError("unreachable")

        app.listen_for_session_async = listen
        transport = SLIMTransport(
            routable_name="org/ns/injected",
            endpoint=_ENDPOINT,
            slim_app=app,
            slim_connection_id=7,
        )
        transport.set_callback(_noop)
        await asyncio.wait_for(listening.wait(), 1)
        listener = transport._listener_task
        assert listener is not None

        await transport.close()

        assert listener.done()

    @pytest.mark.asyncio
    async def test_a_listener_that_will_not_stop_is_abandoned(
        self, service: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(slim_transport, "_CLOSE_TIMEOUT", 0.05)
        (closed, live), _ = await _listening("closed", "live")
        release = False

        async def stubborn() -> None:
            while True:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    if release:
                        raise

        old_listener = closed._listener_task
        assert old_listener is not None
        old_listener.cancel()
        stubborn_task = asyncio.create_task(stubborn())
        closed._listener_task = stubborn_task
        await asyncio.sleep(0)

        await asyncio.wait_for(closed.close(), 1)

        assert not stubborn_task.done()
        release = True
        stubborn_task.cancel()
        await asyncio.gather(stubborn_task, return_exceptions=True)
        await live.close()


class TestSessionManagerCloseAll:
    @staticmethod
    def _manager() -> tuple[SessionManager, MagicMock]:
        app = MagicMock()
        handle = MagicMock(wait_async=AsyncMock())
        app.delete_session_async = AsyncMock(return_value=handle)
        manager = SessionManager()
        manager.set_slim(app, 7)
        return manager, app

    @staticmethod
    def _session(session_id: int) -> MagicMock:
        session = MagicMock()
        session.session_id.return_value = session_id
        return session

    @pytest.mark.asyncio
    async def test_closes_every_cached_session(self) -> None:
        manager, app = self._manager()
        first, second = self._session(1), self._session(2)
        manager._sessions = {"a": first, "b": second}

        await manager.close_all()

        assert app.delete_session_async.await_count == 2
        assert manager._sessions == {}

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_others(self) -> None:
        manager, app = self._manager()
        broken, fine = self._session(1), self._session(2)
        calls = itertools.count()

        def fails_once() -> int:
            if next(calls) == 0:
                raise RuntimeError("closed")
            return 1

        broken.session_id.side_effect = fails_once
        manager._sessions = {"a": broken, "b": fine}

        await manager.close_all()

        app.delete_session_async.assert_awaited_once_with(fine)

    @pytest.mark.asyncio
    async def test_without_a_slim_app_it_does_nothing(self) -> None:
        manager = SessionManager()
        manager._sessions = {"a": self._session(1)}

        await manager.close_all()

        assert len(manager._sessions) == 1
