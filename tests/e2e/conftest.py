# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
import signal
import subprocess
import time

import pytest

TRANSPORT_CONFIGS = {
    "NATS": "localhost:4222",
    "SLIM": "http://localhost:46357",
}


# ---------------------------------------------------------------------------
# Shared subprocess helper
# ---------------------------------------------------------------------------


def _spawn_server(procs, script, transport, endpoint, extra_args=None):
    """Launch a test server subprocess and track it for cleanup."""
    cmd = [
        "uv",
        "run",
        "python",
        script,
    ]
    if transport is not None:
        cmd.extend(["--transport", transport])
    cmd.extend(["--endpoint", endpoint])
    if extra_args:
        cmd.extend(extra_args)

    proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
    procs.append(proc)
    time.sleep(1)
    return proc


def _cleanup_procs(procs):
    """Terminate all tracked subprocesses."""
    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)


def _reset_slim_globals():
    """Reset global SLIM state so the next test gets a fresh instance.

    slim_bindings caches the event loop set via ``uniffi_set_event_loop``.
    The SDK's ``get_or_create_slim_instance`` caches service/app/connection
    globals.  Both must be cleared between tests that run on separate
    asyncio event loops (pytest-asyncio creates a new loop per test).
    """
    import slim_bindings
    from agntcy_app_sdk.transport.slim import common as slim_common

    # Disconnect ALL connections from SLIM service
    try:
        service = slim_bindings.get_global_service()
        # Disconnect connection 0 (typically client connection)
        try:
            service.disconnect(0)
        except Exception:
            pass
        # Disconnect connection 1 (typically server connection)
        try:
            service.disconnect(1)
        except Exception:
            pass
        # Disconnect our cached connection if it exists
        if slim_common.global_connection_id is not None:
            try:
                service.disconnect(slim_common.global_connection_id)
            except Exception:
                pass
    except Exception:
        pass  # Ignore errors during cleanup

    # Clear the cached event loop so subsequent calls fall back to
    # asyncio.get_running_loop() and pick up the new test's loop.
    slim_bindings._slim_bindings.slim_bindings._UNIFFI_GLOBAL_EVENT_LOOP = None

    # Clear the cached SLIM singleton so a fresh connection is created.
    slim_common.global_slim = None
    slim_common.global_slim_service = None
    slim_common.global_connection_id = None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def run_a2a_server():
    procs = []

    def _run(
        transport,
        endpoint,
        version="1.0.0",
        name="default/default/Hello_World_Agent_1.0.0",
        topic="",
    ):
        return _spawn_server(
            procs,
            "tests/server/a2a_starlette_server.py",
            transport,
            endpoint,
            extra_args=[
                "--name",
                name,
                "--topic",
                topic,
                "--version",
                version,
            ],
        )

    yield _run
    _cleanup_procs(procs)


@pytest.fixture
def run_mcp_server():
    procs = []

    def _run(transport, endpoint, name="default/default/mcp"):
        return _spawn_server(
            procs,
            "tests/server/mcp_server.py",
            transport,
            endpoint,
            extra_args=["--name", name],
        )

    yield _run
    _cleanup_procs(procs)


@pytest.fixture
def run_fast_mcp_server():
    procs = []

    def _run(transport, endpoint, name="default/default/fastmcp"):
        return _spawn_server(
            procs,
            "tests/server/fast_mcp_server.py",
            transport,
            endpoint,
            extra_args=["--name", name],
        )

    yield _run
    _cleanup_procs(procs)


@pytest.fixture(autouse=True)
def reset_slim_state_before_test():
    """Reset SLIM globals before each test to ensure clean state."""
    _reset_slim_globals()
    yield


@pytest.fixture
def run_a2a_slimrpc_server():
    procs = []

    def _run(
        endpoint,
        name="default/default/Hello_World_Agent_1.0.0",
        version="1.0.0",
    ):
        return _spawn_server(
            procs,
            "tests/server/a2a_slimrpc_server.py",
            transport=None,
            endpoint=endpoint,
            extra_args=[
                "--name",
                name,
                "--version",
                version,
            ],
        )

    yield _run
    _cleanup_procs(procs)
    _reset_slim_globals()
