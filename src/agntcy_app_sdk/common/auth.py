# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import base64
import concurrent.futures
import json
import os
import threading
import time

from agntcy.identity.service.v1alpha1.auth_service_pb2 import (
    AuthorizeRequest,
    TokenRequest,
)
from identityservice.sdk import IdentityServiceSdk

from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)

# A cached token is replaced this many seconds before it expires, so it is
# not rejected in transit.
_TOKEN_REFRESH_MARGIN = 30.0

# A cached token is replaced after this many seconds at most, whatever its
# expiry, so a token revoked early stops being reused even where no rejection
# reaches the cache.
_TOKEN_MAX_AGE = 300.0

# Seconds a fetch may take: the gRPC deadline shared by its two calls, and
# how long a caller waits for it.
_FETCH_TIMEOUT = 10.0

_TokenKey = tuple[str, str | None]

# (API key, tool name) -> (token, Unix time after which it is fetched again)
_token_cache: dict[_TokenKey, tuple[str, float]] = {}

# Fetches in progress, shared by every caller of the same key.  Thread-safe
# futures, so callers on different event loops can share them.
_in_flight: dict[_TokenKey, concurrent.futures.Future[str | None]] = {}
_in_flight_lock = threading.Lock()

# Bumped by invalidate_access_token(), under _in_flight_lock; a fetch started
# before the bump does not cache its token.
_epoch: dict[_TokenKey, int] = {}


def is_identity_auth_enabled() -> bool:
    """Check if identity authentication is enabled based on environment variables."""

    return (
        os.getenv("IDENTITY_AUTH_ENABLED", "false").lower() in ["true", "enabled"]
        and os.getenv("IDENTITY_SERVICE_API_KEY", "") != ""
    )


def _token_expiry(token: str) -> float | None:
    """The ``exp`` claim of a JWT, read without verifying it; ``None`` if absent.

    Only used to decide when to fetch a new token; the server verifies it.
    """
    try:
        payload = token.split(".")[1]
        claims = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
        exp = claims["exp"]
    except Exception:
        return None
    if isinstance(exp, bool) or not isinstance(exp, (int, float)):
        return None
    return float(exp)


def _token_key(tool_name: str | None) -> _TokenKey:
    return (os.getenv("IDENTITY_SERVICE_API_KEY", ""), tool_name)


def invalidate_access_token(tool_name: str | None = None) -> None:
    """Drop the cached token for this agent (or for *tool_name*).

    Called when a server rejects the token (HTTP 401 / 403), so the next call
    fetches a fresh one instead of reusing a token that was revoked early.
    A fetch already in progress still answers its callers, but does not cache.
    """
    key = _token_key(tool_name)
    with _in_flight_lock:
        _epoch[key] = _epoch.get(key, 0) + 1
        _token_cache.pop(key, None)


def _fetch_access_token(tool_name: str | None) -> str | None:
    """``IdentityServiceSdk.access_token()`` with a gRPC deadline.

    The SDK sets none, so a hung Identity Service would hold the fetch thread
    forever.  Both calls share one ``_FETCH_TIMEOUT`` budget.
    """
    auth = IdentityServiceSdk().get_auth_service()
    deadline = time.monotonic() + _FETCH_TIMEOUT
    try:
        code = auth.Authorize(
            AuthorizeRequest(tool_name=tool_name), timeout=_FETCH_TIMEOUT
        ).authorization_code
        token: str | None = auth.Token(
            TokenRequest(authorization_code=code),
            timeout=max(deadline - time.monotonic(), 0.1),
        ).access_token
        return token
    except Exception as e:
        raise RuntimeError(
            f"Failed to authorize agentic service with tool {tool_name}: {e}"
        ) from e


def _fetch_and_cache(
    key: _TokenKey,
    tool_name: str | None,
    future: concurrent.futures.Future[str | None],
    epoch: int,
) -> None:
    """Fetch a token into *future* and the cache; runs in a worker thread.

    Only the fetch that still owns ``_in_flight[key]``, started since the last
    invalidation, writes the cache, so an abandoned fetch that finishes late
    cannot replace a newer token or restore a rejected one.  The fetch leaves
    ``_in_flight`` before *future* resolves, so a caller that has its result
    never finds it still in progress.
    """
    token: str | None = None
    error: BaseException | None = None
    refresh_at: float | None = None
    try:
        token = _fetch_access_token(tool_name)
        if token:
            expiry = _token_expiry(token)
            if expiry is not None:
                refresh_at = min(
                    expiry - _TOKEN_REFRESH_MARGIN, time.time() + _TOKEN_MAX_AGE
                )
    except BaseException as e:
        error = e
    with _in_flight_lock:
        owner = _in_flight.get(key) is future
        if owner and error is None and _epoch.get(key, 0) == epoch:
            if token and refresh_at is not None:
                _token_cache[key] = (token, refresh_at)
            else:
                _token_cache.pop(key, None)
        if owner:
            del _in_flight[key]
    if error is not None:
        future.set_exception(error)
    else:
        future.set_result(token)


async def get_access_token(tool_name: str | None = None) -> str | None:
    """An Identity Service access token for this agent (or for *tool_name*).

    The Identity Service is called in a worker thread, so a slow response does
    not stall the event loop.  A token with an ``exp`` claim is reused until
    shortly before it expires, and for at most ``_TOKEN_MAX_AGE`` seconds; one
    without is fetched again on every call.
    Concurrent calls that find no valid token share one fetch: they all get
    its token, or its error.  A caller that is cancelled stops waiting without
    cancelling the fetch for the others.  A fetch that does not finish within
    ``_FETCH_TIMEOUT`` seconds is abandoned, and the next call starts a new one;
    its gRPC deadline ends the abandoned thread about as soon.

    Raises:
        TimeoutError: the Identity Service did not answer in time.
        Exception: whatever the Identity Service SDK raises.
    """
    key = _token_key(tool_name)
    cached = _token_cache.get(key)
    if cached is not None and time.time() < cached[1]:
        return cached[0]

    with _in_flight_lock:
        cached = _token_cache.get(key)
        if cached is not None and time.time() < cached[1]:
            return cached[0]
        future = _in_flight.get(key)
        if future is None:
            future = concurrent.futures.Future()
            _in_flight[key] = future
            start = True
        else:
            start = False
        epoch = _epoch.get(key, 0)
    if start:
        threading.Thread(
            target=_fetch_and_cache,
            args=(key, tool_name, future, epoch),
            name="identity-token-fetch",
            daemon=True,
        ).start()
    waiter = asyncio.wrap_future(future)
    # Retrieve the result even if this caller stops waiting, so asyncio does
    # not log "Future exception was never retrieved".
    waiter.add_done_callback(lambda f: f.cancelled() or f.exception())
    try:
        async with asyncio.timeout(_FETCH_TIMEOUT):
            return await asyncio.shield(waiter)
    except TimeoutError:
        with _in_flight_lock:
            if _in_flight.get(key) is future:
                del _in_flight[key]
        raise


async def add_identity_header(headers: dict[str, str]) -> None:
    """Add this agent's bearer token to *headers* when identity auth is on.

    An ``Authorization`` the caller already set (any case) is kept.  A failed
    fetch is logged and the request goes out without a token.
    """
    if not is_identity_auth_enabled() or any(
        k.lower() == "authorization" for k in headers
    ):
        return
    try:
        access_token = await get_access_token()
    except Exception as e:
        logger.error(f"Failed to get access token for agent: {e}")
        return
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
