# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tracing instrumentation must not run before Observe.init(), and must never
break the application when an instrumentor fails."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from agntcy_app_sdk.common import tracing
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.transport.nats.transport import NatsTransport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

# Test-only placeholder, not a credential.
_TEST_SECRET = "x" * 32

_TRACER_WRAPPER = "ioa_observe.sdk.tracing.tracing.TracerWrapper"
_SLIM_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.slim.SLIMInstrumentor"
_NATS_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.nats.NATSInstrumentor"
_A2A_INSTRUMENTOR = "ioa_observe.sdk.instrumentations.a2a.A2AInstrumentor"


class _NoTracer:
    """TracerWrapper before Observe.init(): no singleton yet."""

    def __new__(cls):
        pytest.fail("TracerWrapper constructed before Observe.init()")


class _ReadyTracer:
    """TracerWrapper after Observe.init()."""

    instance = None

    def get_tracer(self):
        return object()


class _Log:
    """Records tracing.logger calls."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def _record(self, level: str, msg: str, *args) -> None:
        self.records.append((level, msg % args))

    def debug(self, msg, *args):
        self._record("debug", msg, *args)

    def warning(self, msg, *args):
        self._record("warning", msg, *args)

    def exception(self, msg, *args):
        self._record("exception", msg, *args)

    def at(self, level: str) -> list[str]:
        return [msg for lvl, msg in self.records if lvl == level]


def _instrumentor(calls: list[str], *, installs: bool = True, raises=None):
    class _Instrumentor:
        is_instrumented_by_opentelemetry = False

        def instrument(self) -> None:
            calls.append("instrument")
            if raises is not None:
                raise raises
            self.is_instrumented_by_opentelemetry = installs

    return _Instrumentor


def _refuse(name: str):
    return lambda: pytest.fail(f"{name} constructed")


@pytest.fixture(autouse=True)
def _isolate_tracing_state():
    saved = (
        set(tracing._instrumented),
        set(tracing._failed),
        dict(tracing._pending),
    )
    saved_requested = tracing._requested
    tracing._requested = False
    for state in (tracing._instrumented, tracing._failed, tracing._pending):
        state.clear()
    try:
        yield
    finally:
        tracing._requested = saved_requested
        for state, value in zip(
            (tracing._instrumented, tracing._failed, tracing._pending), saved
        ):
            state.clear()
            state.update(value)


@pytest.fixture
def log(monkeypatch) -> _Log:
    recorder = _Log()
    monkeypatch.setattr(tracing, "logger", recorder)
    return recorder


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")


def _slim(name: str = "c") -> SLIMTransport:
    return SLIMTransport(
        endpoint="http://localhost:46357",
        routable_name=f"a/b/{name}",
        shared_secret_identity=_TEST_SECRET,
    )


def _ready(monkeypatch) -> None:
    monkeypatch.setattr(_TRACER_WRAPPER, _ReadyTracer)


# ---------------------------------------------------------------------------
# Before Observe.init()
# ---------------------------------------------------------------------------


def test_not_requested_does_nothing(monkeypatch, log):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _refuse("SLIMInstrumentor"))

    _slim()
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented
    assert log.records == []
    assert tracing._pending == {tracing.SLIM_INSTRUMENTOR: "SLIMTransport"}


def test_env_without_init_skips_slim_and_warns_once(monkeypatch, traced, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _refuse("SLIMInstrumentor"))

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented

    warnings = log.at("warning")
    assert len(warnings) == 1
    assert "Observe.init() has not run" in warnings[0]
    assert "SLIMTransport" in warnings[0]


def test_env_without_init_warns_once_per_instrumentor(monkeypatch, traced, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _refuse("SLIMInstrumentor"))
    monkeypatch.setattr(_NATS_INSTRUMENTOR, _refuse("NATSInstrumentor"))
    monkeypatch.setattr(_A2A_INSTRUMENTOR, _refuse("A2AInstrumentor"))

    _slim()
    NatsTransport(endpoint="nats://localhost:4222")
    A2AClientFactory()._initialize_tracing_if_enabled()

    assert tracing.NATS_INSTRUMENTOR not in tracing._instrumented
    warnings = log.at("warning")
    assert len(warnings) == 3
    for component in ("SLIMTransport", "NatsTransport", "A2A client"):
        assert any(component in w for w in warnings), component


def test_ioa_observe_missing_warns_once(monkeypatch, traced, log):
    monkeypatch.setitem(sys.modules, "ioa_observe.sdk.tracing.tracing", None)

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented
    NatsTransport(endpoint="nats://localhost:4222")

    warnings = log.at("warning")
    assert len(warnings) == 2
    assert all("ioa_observe is not installed" in w for w in warnings)
    assert tracing._pending == {}


# ---------------------------------------------------------------------------
# After Observe.init()
# ---------------------------------------------------------------------------


def test_ready_instruments_once_across_transports(monkeypatch, traced, log):
    _ready(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(calls))

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR in tracing._instrumented
    assert calls == ["instrument"]
    assert log.at("warning") == []


def test_factory_installs_instrumentors_skipped_before_init(monkeypatch, traced, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    slim_calls: list[str] = []
    nats_calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(slim_calls))
    monkeypatch.setattr(_NATS_INSTRUMENTOR, _instrumentor(nats_calls))

    _slim()
    NatsTransport(endpoint="nats://localhost:4222")
    assert slim_calls == nats_calls == []

    monkeypatch.setattr(
        "ioa_observe.sdk.Observe.init", lambda name, **kwargs: _ready(monkeypatch)
    )
    AgntcyFactory("trace-test", enable_tracing=True)

    assert slim_calls == nats_calls == ["instrument"]
    assert tracing._pending == {}


def test_setup_picks_up_direct_observe_init(monkeypatch, traced, log):
    """Observe.init() called directly, without the factory, after construction."""
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    slim_calls: list[str] = []
    nats_calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(slim_calls))
    monkeypatch.setattr(_NATS_INSTRUMENTOR, _instrumentor(nats_calls))

    slim_transport = _slim()
    nats_transport = NatsTransport(endpoint="nats://localhost:4222")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented

    _ready(monkeypatch)
    slim_transport._slim_app = object()  # already connected; setup() skips I/O
    nats_transport._nc = MagicMock(is_connected=True)
    asyncio.run(slim_transport.setup())
    asyncio.run(nats_transport.setup())

    assert tracing.SLIM_INSTRUMENTOR in tracing._instrumented
    assert tracing.NATS_INSTRUMENTOR in tracing._instrumented
    assert slim_calls == nats_calls == ["instrument"]


def test_a2a_client_retries_on_each_create(monkeypatch, traced, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    calls: list[str] = []
    monkeypatch.setattr(_A2A_INSTRUMENTOR, _instrumentor(calls))
    factory = A2AClientFactory()

    factory._initialize_tracing_if_enabled()
    assert calls == []

    _ready(monkeypatch)
    factory._initialize_tracing_if_enabled()
    factory._initialize_tracing_if_enabled()

    assert calls == ["instrument"]


def test_direct_install_clears_pending_entry(monkeypatch, traced, log):
    """A later instrument_pending() must not install the same instrumentor again."""
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(calls))

    _slim("a")
    _ready(monkeypatch)
    _slim("b")
    tracing.instrument_pending()
    tracing.instrument_pending()

    assert calls == ["instrument"]
    assert tracing._pending == {}


def test_instrument_pending_is_a_noop_when_nothing_is_pending(monkeypatch, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)

    tracing.instrument_pending()
    tracing.instrument_pending()

    assert log.records == []


# ---------------------------------------------------------------------------
# Instrumentor failures must not break the application
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [RuntimeError("boom"), AttributeError("'NoneType' has no 'get_tracer'")],
)
def test_raising_instrument_is_logged_and_not_retried(monkeypatch, traced, log, error):
    _ready(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(calls, raises=error))

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented

    assert calls == ["instrument"]
    errors = log.at("exception")
    assert len(errors) == 1
    assert "SLIMTransport is not traced" in errors[0]


def test_raising_constructor_is_logged(monkeypatch, traced, log):
    _ready(monkeypatch)

    def _broken():
        raise AttributeError("'NoneType' object has no attribute 'get_tracer'")

    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _broken)

    _slim()
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented
    assert len(log.at("exception")) == 1


def test_unimportable_instrumentor_is_logged_and_not_retried(monkeypatch, traced, log):
    _ready(monkeypatch)
    monkeypatch.setitem(sys.modules, "ioa_observe.sdk.instrumentations.slim", None)

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented

    assert len(log.at("exception")) == 1


def test_dependency_conflict_is_reported_not_claimed(monkeypatch, traced, log):
    """BaseInstrumentor.instrument() returns without installing on a conflict."""
    _ready(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(calls, installs=False))

    _slim("a")
    _slim("b")
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented

    assert calls == ["instrument"]
    warnings = log.at("warning")
    assert len(warnings) == 1
    assert "did not instrument" in warnings[0]


def test_failure_in_one_instrumentor_does_not_block_others(monkeypatch, traced, log):
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    nats_calls: list[str] = []
    monkeypatch.setattr(
        _SLIM_INSTRUMENTOR, _instrumentor([], raises=RuntimeError("boom"))
    )
    monkeypatch.setattr(_NATS_INSTRUMENTOR, _instrumentor(nats_calls))
    _slim()
    NatsTransport(endpoint="nats://localhost:4222")

    _ready(monkeypatch)
    tracing.instrument_pending()

    assert nats_calls == ["instrument"]
    assert len(log.at("exception")) == 1


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_keeps_request_in_memory(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_requested", False)
    monkeypatch.setattr("ioa_observe.sdk.Observe.init", lambda name, **kwargs: None)

    AgntcyFactory("trace-test", enable_tracing=True)

    assert "TRACING_ENABLED" not in os.environ
    assert tracing.tracing_requested() is True


def test_failed_observe_init_does_not_request_tracing(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_requested", False)

    def _failing_init(name, **kwargs):
        raise RuntimeError("init failed")

    monkeypatch.setattr("ioa_observe.sdk.Observe.init", _failing_init)
    with pytest.raises(RuntimeError):
        AgntcyFactory("trace-test", enable_tracing=True)

    assert tracing.tracing_requested() is False
    assert "TRACING_ENABLED" not in os.environ


def test_child_process_does_not_inherit_factory_request(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setattr("ioa_observe.sdk.Observe.init", lambda name, **kwargs: None)
    AgntcyFactory("trace-test", enable_tracing=True)

    result = subprocess.run(
        [sys.executable, "-c", "import os; print(os.environ.get('TRACING_ENABLED'))"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.stdout.strip() == "None"


def test_observe_init_installs_instrumentors_remembered_before_request(
    monkeypatch, log
):
    """setup() before tracing is requested, then Observe.init(), with no manual flush."""
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    monkeypatch.setattr(tracing, "_requested", False)
    monkeypatch.setattr(_TRACER_WRAPPER, _NoTracer)
    calls: list[str] = []
    monkeypatch.setattr(_SLIM_INSTRUMENTOR, _instrumentor(calls))

    _slim()
    assert tracing.SLIM_INSTRUMENTOR not in tracing._instrumented
    assert calls == []
    assert tracing.SLIM_INSTRUMENTOR in tracing._pending

    from ioa_observe.sdk import Observe

    def _init(*args, **kwargs):
        _ready(monkeypatch)
        return None

    _init._agntcy_flushes_pending = False
    monkeypatch.setattr(Observe, "init", staticmethod(_init))
    tracing._hook_observe_init()
    Observe.init("trace-test")

    assert calls == ["instrument"]
    assert tracing._pending == {}


# ---------------------------------------------------------------------------
# Real ioa-observe SDK, no mocks (separate processes: instrumentation is global)
# ---------------------------------------------------------------------------


def _run(script: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TRACING_ENABLED"] = "true"
    env["OBSERVE_TELEMETRY"] = "false"
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_issue_132_constructor_survives_inherited_env():
    """The reported one-liner: the variable is set and Observe.init() never ran."""
    result = _run(
        "from agntcy_app_sdk.transport.slim.transport import SLIMTransport\n"
        "SLIMTransport(endpoint='http://localhost:46357', routable_name='a/b/c',"
        f" shared_secret_identity={_TEST_SECRET!r})\n"
        "print('CONSTRUCTED_OK')\n"
    )

    assert result.returncode == 0, result.stderr
    assert "CONSTRUCTED_OK" in result.stdout
    assert "get_tracer" not in result.stderr


def test_real_sdk_direct_init_after_construction():
    """Readiness detection against the installed SDK, so SDK drift fails here."""
    result = _run(
        f"""
import asyncio
from unittest.mock import MagicMock
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from ioa_observe.sdk import Observe
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.transport.nats.transport import NatsTransport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

slim = SLIMTransport(endpoint='http://localhost:46357', routable_name='a/b/c',
                     shared_secret_identity={_TEST_SECRET!r})
nats = NatsTransport(endpoint='nats://localhost:4222')
a2a = A2AClientFactory()
a2a._initialize_tracing_if_enabled()
from agntcy_app_sdk.common.tracing import (
    NATS_INSTRUMENTOR,
    SLIM_INSTRUMENTOR,
    _instrumented,
)
assert SLIM_INSTRUMENTOR not in _instrumented
assert NATS_INSTRUMENTOR not in _instrumented

Observe.init('real-sdk-test', exporter=InMemorySpanExporter())

slim._slim_app = object()
nats._nc = MagicMock(is_connected=True)
asyncio.run(slim.setup())
asyncio.run(nats.setup())
a2a._initialize_tracing_if_enabled()

from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor
from ioa_observe.sdk.instrumentations.nats import NATSInstrumentor
from ioa_observe.sdk.instrumentations.slim import SLIMInstrumentor

for cls in (SLIMInstrumentor, NATSInstrumentor, A2AInstrumentor):
    assert cls().is_instrumented_by_opentelemetry, cls.__name__
print('REAL_SDK_OK')
"""
    )

    assert result.returncode == 0, result.stderr
    assert "REAL_SDK_OK" in result.stdout
    assert "already instrumented" not in result.stdout + result.stderr
