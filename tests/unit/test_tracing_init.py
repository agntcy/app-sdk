# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tracing instrumentation must not run before Observe.init() in this process."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agntcy_app_sdk.common import tracing
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.transport.nats.transport import NatsTransport
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

# Test-only placeholder, not a credential.
_TEST_SECRET = "x" * 32


class _NoTracer:
    """Stand-in for TracerWrapper before Observe.init()."""


class _ReadyTracer:
    """Stand-in whose singleton already has a provider."""

    class _Instance:
        _TracerWrapper__tracer_provider = object()

    instance = _Instance()


@pytest.fixture(autouse=True)
def _isolate_tracing_flag():
    requested = tracing._requested
    logged = tracing._skip_logged
    tracing._requested = False
    tracing._skip_logged = False
    try:
        yield
    finally:
        tracing._requested = requested
        tracing._skip_logged = logged


def _slim() -> SLIMTransport:
    return SLIMTransport(
        endpoint="http://localhost:46357",
        routable_name="a/b/c",
        shared_secret_identity=_TEST_SECRET,
    )


def test_env_without_init_does_not_instrument_slim(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setattr("ioa_observe.sdk.tracing.tracing.TracerWrapper", _NoTracer)
    monkeypatch.setattr(
        "ioa_observe.sdk.instrumentations.slim.SLIMInstrumentor",
        lambda: pytest.fail("SLIMInstrumentor constructed"),
    )

    transport = _slim()

    assert transport.enable_opentelemetry is False


def test_env_without_init_does_not_instrument_nats(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setattr("ioa_observe.sdk.tracing.tracing.TracerWrapper", _NoTracer)
    monkeypatch.setattr(
        "ioa_observe.sdk.instrumentations.nats.NATSInstrumentor",
        lambda: pytest.fail("NATSInstrumentor constructed"),
    )

    transport = NatsTransport(endpoint="nats://localhost:4222")

    assert not hasattr(transport, "tracing_enabled")


def test_env_without_init_does_not_instrument_a2a_client(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setattr("ioa_observe.sdk.tracing.tracing.TracerWrapper", _NoTracer)
    monkeypatch.setattr(
        "ioa_observe.sdk.instrumentations.a2a.A2AInstrumentor",
        lambda: pytest.fail("A2AInstrumentor constructed"),
    )

    A2AClientFactory()._initialize_tracing_if_enabled()


def test_ready_tracer_still_instruments_slim(monkeypatch):
    monkeypatch.setenv("TRACING_ENABLED", "true")
    monkeypatch.setattr("ioa_observe.sdk.tracing.tracing.TracerWrapper", _ReadyTracer)
    calls: list[str] = []

    class _Instrumentor:
        def instrument(self) -> None:
            calls.append("instrument")

    monkeypatch.setattr(
        "ioa_observe.sdk.instrumentations.slim.SLIMInstrumentor",
        _Instrumentor,
    )

    _slim()

    assert calls == ["instrument"]


def test_factory_does_not_export_tracing_enabled(monkeypatch):
    monkeypatch.delenv("TRACING_ENABLED", raising=False)
    inited: list[str] = []

    def _init(name, **kwargs):
        inited.append(name)

    monkeypatch.setattr("ioa_observe.sdk.Observe.init", _init)

    AgntcyFactory("trace-test", enable_tracing=True)

    assert "TRACING_ENABLED" not in os.environ
    assert tracing.tracing_requested() is True
    assert inited == ["trace-test"]


def test_nats_and_a2a_instrument_when_a_provider_exists():
    """A tracer provider makes instrument() install the real wrappers."""
    script = """
import agntcy_app_sdk
from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor
from ioa_observe.sdk.instrumentations.nats import NATSInstrumentor
from ioa_observe.sdk.tracing.tracing import TracerWrapper

class _Provider:
    def get_tracer(self, name):
        return object()

TracerWrapper.instance = type(
    "Instance", (), {"_TracerWrapper__tracer_provider": _Provider()}
)()

for cls in (NATSInstrumentor, A2AInstrumentor):
    instrumentor = cls()
    instrumentor.instrument()
    assert instrumentor.is_instrumented_by_opentelemetry is True, cls.__name__
print("INSTRUMENTED_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INSTRUMENTED_OK" in result.stdout


def test_nats_and_a2a_instrumentors_construct_without_observe_init():
    """The instrumentor classes themselves, not only the SDK wrappers.

    ``instrument()`` must not install wrappers yet: A2A's wrappers call
    ``_global_tracer.start_as_current_span`` with no guard. The flag stays
    clear so a later ``instrument()`` after ``Observe.init()`` still runs.
    """
    env = os.environ.copy()
    env["TRACING_ENABLED"] = "true"
    script = """
import agntcy_app_sdk
from ioa_observe.sdk.instrumentations.a2a import A2AInstrumentor
from ioa_observe.sdk.instrumentations.nats import NATSInstrumentor

for cls in (NATSInstrumentor, A2AInstrumentor):
    instrumentor = cls()
    instrumentor.instrument()
    assert instrumentor.is_instrumented_by_opentelemetry is False, cls.__name__
print("INSTRUMENTORS_OK")
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "INSTRUMENTORS_OK" in result.stdout
    assert "get_tracer" not in result.stderr


def test_issue_132_constructor_survives_inherited_env():
    """The reported one-liner: the variable is set and Observe.init() never ran."""
    env = os.environ.copy()
    env["TRACING_ENABLED"] = "true"
    script = (
        "from agntcy_app_sdk.transport.slim.transport import SLIMTransport\n"
        "SLIMTransport(endpoint='http://localhost:46357', routable_name='a/b/c',"
        f" shared_secret_identity={_TEST_SECRET!r})\n"
        "print('CONSTRUCTED_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "CONSTRUCTED_OK" in result.stdout
    assert "get_tracer" not in result.stderr
