# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Instrument ioa_observe only after ``Observe.init()`` has run in this process.

``AgntcyFactory(enable_tracing=True)`` remembers that request in this process
only. It does not set ``TRACING_ENABLED``, so child processes do not inherit
it. An environment variable the user already exported is still honored.

Components are remembered even when tracing is not requested yet. After
``Observe.init()`` returns, those instrumentors are installed if a tracer
exists. The ioa_observe instrumentors call ``TracerWrapper().get_tracer()``
in their constructor, which raises ``AttributeError`` until ``Observe.init()``
has built a tracer provider.

Tracing must never break the application: every failure is logged once and
the instrumentor is not retried.
"""

from __future__ import annotations

import importlib
import os
import threading

from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)

SLIM_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.slim:SLIMInstrumentor"
NATS_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.nats:NATSInstrumentor"
A2A_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.a2a:A2AInstrumentor"

_lock = threading.Lock()
_instrumented: set[str] = set()
_failed: set[str] = set()
# Instrumentor path -> component that first asked for it.
_pending: dict[str, str] = {}
# Set by AgntcyFactory(enable_tracing=True). Never copied into os.environ.
_requested = False


def request_tracing() -> None:
    """Remember that this process asked for tracing.

    Does not set ``TRACING_ENABLED``. Child processes do not inherit the
    request. Call this only after ``Observe.init()`` returns, so a raised
    init leaves the flag clear.
    """
    global _requested
    _requested = True


def tracing_requested() -> bool:
    if _requested:
        return True
    return os.environ.get("TRACING_ENABLED", "false").lower() == "true"


def _observe_state() -> str:
    """``ready``, ``missing`` (ioa_observe not installed), or ``uninitialized``."""
    try:
        from ioa_observe.sdk.tracing.tracing import TracerWrapper
    except ImportError:
        return "missing"

    # TracerWrapper() caches a singleton even when Observe.init() has not
    # configured it, and a later Observe.init() would reuse that empty one.
    if not hasattr(TracerWrapper, "instance"):
        return "uninitialized"
    try:
        TracerWrapper().get_tracer()
    except AttributeError:
        return "uninitialized"
    return "ready"


def _install(component: str, instrumentor: str) -> None:
    module_name, class_name = instrumentor.split(":")
    try:
        instance = getattr(importlib.import_module(module_name), class_name)()
        instance.instrument()
        installed = instance.is_instrumented_by_opentelemetry
    except Exception:
        _failed.add(instrumentor)
        logger.exception("%s failed; %s is not traced", class_name, component)
        return

    if installed:
        _instrumented.add(instrumentor)
        logger.debug("%s instrumented for tracing", component)
    else:
        # BaseInstrumentor.instrument() logs dependency conflicts and returns.
        _failed.add(instrumentor)
        logger.warning(
            "%s did not instrument (see the OpenTelemetry log); %s is not traced",
            class_name,
            component,
        )


def _flush_pending() -> str | None:
    """Install pending instrumentors if possible. Caller holds ``_lock``."""
    if not _pending:
        return None

    state = _observe_state()
    if state == "ready":
        pending = dict(_pending)
        _pending.clear()
        for instrumentor, component in pending.items():
            _install(component, instrumentor)
    elif state == "missing":
        logger.warning(
            "Tracing was requested but ioa_observe is not installed; %s is not traced",
            ", ".join(sorted(set(_pending.values()))),
        )
        _failed.update(_pending)
        _pending.clear()
    return state


def instrument_if_ready(component: str, instrumentor: str) -> bool:
    """Remember *instrumentor* (``module:Class``) and install it when tracing is ready.

    The component is recorded even when tracing has not been requested yet.
    ``Observe.init()`` installs what was remembered when it returns and a tracer
    exists. This function also installs it once tracing is requested and a
    tracer exists, including on a later call (e.g. from ``setup()``). Returns
    True once *instrumentor* is installed.
    """
    _hook_observe_init()
    with _lock:
        is_new = (
            instrumentor not in _instrumented
            and instrumentor not in _failed
            and instrumentor not in _pending
        )
        if is_new:
            _pending[instrumentor] = component
        if not tracing_requested():
            return False

        state = _flush_pending()
        if is_new and state == "uninitialized":
            logger.warning(
                "Tracing was requested but Observe.init() has not run in this "
                "process; %s is not traced until it does",
                component,
            )
        return instrumentor in _instrumented


def instrument_pending() -> None:
    """Install instrumentors that were skipped before ``Observe.init()`` ran.

    ``Observe.init()`` does this when it returns. Safe to call repeatedly.
    """
    with _lock:
        _flush_pending()


def _hook_observe_init() -> None:
    """After ``Observe.init`` returns, install instrumentors remembered earlier.

    This does not patch instrumentor classes and does not construct them
    before init. A raised ``init`` does not flush.
    """
    try:
        from ioa_observe.sdk import Observe
    except ImportError:
        return

    current = Observe.init
    if getattr(current, "_agntcy_flushes_pending", False):
        return

    def init(*args, **kwargs):
        result = current(*args, **kwargs)
        instrument_pending()
        return result

    init._agntcy_flushes_pending = True  # type: ignore[attr-defined]
    Observe.init = staticmethod(init)


_hook_observe_init()
