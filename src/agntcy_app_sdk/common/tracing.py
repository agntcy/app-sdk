# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Process-local tracing gate.

``TRACING_ENABLED`` is an environment variable, so a child process inherits
it. The ioa_observe instrumentors call ``TracerWrapper.get_tracer()`` while
they are constructed, and that raises ``AttributeError`` when ``Observe.init()``
has not run in *this* process. Callers instrument only when a tracer provider
already exists, and ``AgntcyFactory(enable_tracing=True)`` records the request
here instead of exporting the variable.
"""

from __future__ import annotations

import os
from typing import Callable

from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)

# Set by AgntcyFactory(enable_tracing=True). Not copied into os.environ.
_requested = False
_skip_logged = False


def request_tracing() -> None:
    """Remember that this process asked for tracing.

    Does not set ``TRACING_ENABLED``. Child processes start with this flag
    clear and do not inherit the request.
    """
    global _requested
    _requested = True


def tracing_requested() -> bool:
    """True when this process asked for tracing, or the env var is set."""
    if _requested:
        return True
    return os.environ.get("TRACING_ENABLED", "false").lower() == "true"


def _tracer_provider():
    """The live tracer provider, or ``(status, None)`` when there isn't one.

    Status is ``missing`` when ioa_observe is not installed and
    ``uninitialized`` when ``Observe.init()`` has not finished. This never
    constructs ``TracerWrapper``: its ``__new__`` stores a singleton even
    when no provider exists, and a later ``Observe.init()`` would reuse it.
    """
    try:
        from ioa_observe.sdk.tracing.tracing import TracerWrapper
    except ImportError:
        return "missing", None

    instance = getattr(TracerWrapper, "instance", None)
    # Name-mangled attribute. get_tracer() dereferences it with no guard.
    provider = (
        getattr(instance, "_TracerWrapper__tracer_provider", None)
        if instance is not None
        else None
    )
    if provider is None:
        return "uninitialized", None
    return "ready", provider


def _tracer_status() -> str:
    """``ready``, ``missing`` (package not installed), or ``uninitialized``."""
    status, _provider = _tracer_provider()
    return status


def _observe_tracer():
    """The ioa_observe tracer, or ``None`` when ``Observe.init()`` has not run."""
    status, provider = _tracer_provider()
    if status != "ready":
        return None
    from ioa_observe.sdk.tracing.tracing import TRACER_NAME

    return provider.get_tracer(TRACER_NAME)


# These constructors call TracerWrapper().get_tracer() with no guard.
# A2A's wrappers then call _global_tracer.start_as_current_span with no guard,
# so instrument() must not install them until a tracer exists.
_UNSAFE_INSTRUMENTORS = (
    ("ioa_observe.sdk.instrumentations.nats", "NATSInstrumentor"),
    ("ioa_observe.sdk.instrumentations.a2a", "A2AInstrumentor"),
    ("ioa_observe.sdk.instrumentations.slim", "SLIMInstrumentor"),
)


def harden_observe_instrumentors() -> None:
    """Make NATS, A2A, and SLIM instrumentors safe to construct with no tracer.

    ioa-observe-sdk builds the tracer inside ``__init__``. Replacing that
    constructor leaves ``_global_tracer`` unset instead of raising, and
    ``instrument()`` does nothing until ``Observe.init()`` has finished so it
    can be called again afterwards.
    """
    for module_name, class_name in _UNSAFE_INSTRUMENTORS:
        try:
            module = __import__(module_name, fromlist=[class_name])
        except ImportError:
            logger.warning("Cannot harden %s; ioa_observe is not installed", class_name)
            continue
        cls = getattr(module, class_name, None)
        if cls is None or getattr(cls, "_agntcy_tracer_safe", False):
            continue
        _harden_instrumentor(module, cls)


def _harden_instrumentor(module, cls) -> None:
    original_instrument = cls.instrument

    def __init__(self) -> None:
        module._global_tracer = _observe_tracer()

    def instrument(self, **kwargs):
        module._global_tracer = _observe_tracer()
        if module._global_tracer is None:
            return None
        return original_instrument(self, **kwargs)

    cls.__init__ = __init__
    cls.instrument = instrument
    cls._agntcy_tracer_safe = True


def instrument_if_ready(component: str, start: Callable[[], None]) -> bool:
    """Run *start* when tracing was requested and a tracer provider exists.

    Returns False without calling *start* when tracing was not requested, or
    when it was requested but this process has no tracer. The skip is logged
    once per process.
    """
    global _skip_logged
    if not tracing_requested():
        return False

    status = _tracer_status()
    if status != "ready":
        if not _skip_logged:
            _skip_logged = True
            if status == "missing":
                logger.warning(
                    "Tracing was requested but ioa_observe is not installed; "
                    "skipping %s instrumentation",
                    component,
                )
            else:
                logger.warning(
                    "Tracing was requested but Observe.init() has not run in "
                    "this process; skipping %s instrumentation",
                    component,
                )
        return False

    start()
    return True
