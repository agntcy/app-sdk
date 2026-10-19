# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""``get_access_token``: fetched off the event loop, reused until near expiry."""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from agntcy_app_sdk.common import auth

pytest_plugins = "pytest_asyncio"


def _jwt(exp: float | None) -> str:
    """An unsigned test JWT; only its ``exp`` claim matters here."""

    def part(data: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()

    claims = {} if exp is None else {"exp": exp}
    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


class _Fetcher:
    """Stands in for the Identity Service call; records each fetch."""

    def __init__(self, *tokens: str) -> None:
        self._tokens = list(tokens)
        self.calls: list[str | None] = []
        self.threads: list[threading.Thread] = []

    def __call__(self, tool_name: str | None) -> str | None:
        self.calls.append(tool_name)
        self.threads.append(threading.current_thread())
        return self._tokens.pop(0)


@pytest.fixture(autouse=True)
def _clean_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
    auth._token_cache.clear()
    yield
    auth._token_cache.clear()
    assert not auth._in_flight


def _use(monkeypatch: pytest.MonkeyPatch, fetcher: _Fetcher) -> None:
    monkeypatch.setattr(auth, "_fetch_access_token", fetcher)


@pytest.mark.asyncio
async def test_valid_token_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    token = _jwt(time.time() + 3600)
    fetcher = _Fetcher(token)
    _use(monkeypatch, fetcher)

    assert await auth.get_access_token() == token
    assert await auth.get_access_token() == token
    assert len(fetcher.calls) == 1


@pytest.mark.asyncio
async def test_token_near_expiry_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    old = _jwt(time.time() + auth._TOKEN_REFRESH_MARGIN - 1)
    new = _jwt(time.time() + 3600)
    fetcher = _Fetcher(old, new)
    _use(monkeypatch, fetcher)

    assert await auth.get_access_token() == old
    assert await auth.get_access_token() == new
    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_long_lived_token_is_refetched_after_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _jwt(time.time() + 86400)
    second = _jwt(time.time() + 86400 + 1)
    fetcher = _Fetcher(first, second)
    _use(monkeypatch, fetcher)
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)

    assert await auth.get_access_token() == first
    now += auth._TOKEN_MAX_AGE - 1
    assert await auth.get_access_token() == first
    now += 2
    assert await auth.get_access_token() == second
    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_invalidated_token_is_fetched_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp = time.time() + 3600
    fetcher = _Fetcher(_jwt(exp), _jwt(exp + 1), _jwt(exp + 2))
    _use(monkeypatch, fetcher)
    agent = await auth.get_access_token()
    tool = await auth.get_access_token(tool_name="weather")

    auth.invalidate_access_token()

    assert await auth.get_access_token() != agent
    assert await auth.get_access_token(tool_name="weather") == tool
    assert fetcher.calls == [None, "weather", None]


def test_invalidating_an_uncached_token_is_a_no_op() -> None:
    auth.invalidate_access_token(tool_name="never-fetched")

    assert not auth._token_cache


@pytest.mark.parametrize("token", [_jwt(None), "opaque-token"])
@pytest.mark.asyncio
async def test_token_without_expiry_is_not_cached(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    fetcher = _Fetcher(token, token)
    _use(monkeypatch, fetcher)

    await auth.get_access_token()
    await auth.get_access_token()

    assert len(fetcher.calls) == 2


@pytest.mark.asyncio
async def test_cache_is_per_tool_and_per_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp = time.time() + 3600
    fetcher = _Fetcher(_jwt(exp), _jwt(exp + 1), _jwt(exp + 2))
    _use(monkeypatch, fetcher)

    agent = await auth.get_access_token()
    tool = await auth.get_access_token(tool_name="weather")
    monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "another-placeholder")
    other_key = await auth.get_access_token()

    assert len({agent, tool, other_key}) == 3
    assert fetcher.calls == [None, "weather", None]


@pytest.mark.asyncio
async def test_failure_propagates_and_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_tool_name: str | None) -> str | None:
        raise RuntimeError("identity service down")

    monkeypatch.setattr(auth, "_fetch_access_token", broken)

    with pytest.raises(RuntimeError, match="identity service down"):
        await auth.get_access_token()
    assert not auth._token_cache


@pytest.mark.asyncio
async def test_fetch_runs_in_a_worker_thread_without_blocking_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(_tool_name: str | None) -> str | None:
        time.sleep(0.3)
        return "tok"

    monkeypatch.setattr(auth, "_fetch_access_token", slow)
    ticks = 0

    async def other_work() -> None:
        nonlocal ticks
        for _ in range(5):
            await asyncio.sleep(0.02)
            ticks += 1

    token, _ = await asyncio.gather(auth.get_access_token(), other_work())

    assert token == "tok"
    assert ticks == 5


@pytest.mark.asyncio
async def test_fetch_is_not_on_the_event_loop_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetcher = _Fetcher("tok")
    _use(monkeypatch, fetcher)

    await auth.get_access_token()

    assert fetcher.threads[0] is not threading.current_thread()


def _gated(result: str | Exception) -> tuple[Any, threading.Event, list[str | None]]:
    """A fetch that waits for *release*, then returns or raises *result*."""
    release = threading.Event()
    calls: list[str | None] = []

    def fetch(tool_name: str | None) -> str | None:
        calls.append(tool_name)
        release.wait(5)
        if isinstance(result, Exception):
            raise result
        return result

    return fetch, release, calls


async def _release_soon(release: threading.Event) -> None:
    await asyncio.sleep(0.05)
    release.set()


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _jwt(time.time() + 3600)
    fetch, release, calls = _gated(token)
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)

    *tokens, _ = await asyncio.gather(
        *(auth.get_access_token() for _ in range(20)), _release_soon(release)
    )

    assert tokens == [token] * 20
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, release, calls = _gated(RuntimeError("identity service down"))
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)

    *results, _ = await asyncio.gather(
        *(auth.get_access_token() for _ in range(5)),
        _release_soon(release),
        return_exceptions=True,
    )

    assert all(
        isinstance(r, RuntimeError) and str(r) == "identity service down"
        for r in results
    )
    assert len(calls) == 1
    assert not auth._token_cache


@pytest.mark.asyncio
async def test_fetch_after_a_failure_tries_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_tool_name: str | None) -> str | None:
        raise RuntimeError("identity service down")

    monkeypatch.setattr(auth, "_fetch_access_token", broken)
    with pytest.raises(RuntimeError):
        await auth.get_access_token()

    fetcher = _Fetcher("tok")
    _use(monkeypatch, fetcher)

    assert await auth.get_access_token() == "tok"


@pytest.mark.asyncio
async def test_cancelled_caller_does_not_cancel_the_shared_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _jwt(time.time() + 3600)
    fetch, release, calls = _gated(token)
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)

    first = asyncio.create_task(auth.get_access_token())
    second = asyncio.create_task(auth.get_access_token())
    await asyncio.sleep(0.01)
    first.cancel()
    release.set()

    assert await second == token
    with pytest.raises(asyncio.CancelledError):
        await first
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_hung_fetch_times_out_and_the_next_call_fetches_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetch, release, calls = _gated("tok")
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)
    monkeypatch.setattr(auth, "_FETCH_TIMEOUT", 0.05)

    try:
        with pytest.raises(TimeoutError):
            await auth.get_access_token()
        assert not auth._in_flight
        with pytest.raises(TimeoutError):
            await auth.get_access_token()
    finally:
        release.set()

    assert len(calls) == 2


@pytest.mark.asyncio
async def test_token_fetched_across_an_invalidation_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _jwt(time.time() + 3600)
    fetch, release, calls = _gated(token)
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)

    pending = asyncio.create_task(auth.get_access_token())
    await asyncio.sleep(0.01)
    auth.invalidate_access_token()
    release.set()

    assert await pending == token
    assert not auth._token_cache


@pytest.mark.asyncio
async def test_abandoned_fetch_that_finishes_late_does_not_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = _jwt(time.time() + 3600)
    fetch, release, _ = _gated(late)
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)
    monkeypatch.setattr(auth, "_FETCH_TIMEOUT", 0.05)
    with pytest.raises(TimeoutError):
        await auth.get_access_token()

    fresh = _jwt(time.time() + 3601)
    _use(monkeypatch, _Fetcher(fresh))
    assert await auth.get_access_token() == fresh
    release.set()
    await asyncio.sleep(0.05)

    assert auth._token_cache[auth._token_key(None)][0] == fresh


def test_fetch_passes_a_deadline_to_both_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MagicMock()
    service.Authorize.return_value = SimpleNamespace(authorization_code="code")
    service.Token.return_value = SimpleNamespace(access_token="tok")
    sdk = MagicMock()
    sdk.get_auth_service.return_value = service
    monkeypatch.setattr(auth, "IdentityServiceSdk", lambda: sdk)

    assert auth._fetch_access_token("weather") == "tok"

    request = service.Authorize.call_args.args[0]
    assert request.tool_name == "weather"
    assert service.Authorize.call_args.kwargs["timeout"] == auth._FETCH_TIMEOUT
    assert service.Token.call_args.args[0].authorization_code == "code"
    assert 0 < service.Token.call_args.kwargs["timeout"] <= auth._FETCH_TIMEOUT


def test_fetch_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    sdk = MagicMock()
    sdk.get_auth_service.return_value.Authorize.side_effect = OSError("down")
    monkeypatch.setattr(auth, "IdentityServiceSdk", lambda: sdk)

    with pytest.raises(RuntimeError, match="tool weather: down") as info:
        auth._fetch_access_token("weather")
    assert isinstance(info.value.__cause__, OSError)


def test_callers_on_different_event_loops_share_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = _jwt(time.time() + 3600)
    fetch, release, calls = _gated(token)
    monkeypatch.setattr(auth, "_fetch_access_token", fetch)
    results: list[str | None] = []

    def run_loop() -> None:
        results.append(asyncio.run(auth.get_access_token()))

    loops = [threading.Thread(target=run_loop) for _ in range(2)]
    for t in loops:
        t.start()
    time.sleep(0.05)
    release.set()
    for t in loops:
        t.join(5)

    assert results == [token, token]
    assert len(calls) == 1
