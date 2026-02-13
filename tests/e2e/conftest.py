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
        "--transport",
        transport,
        "--endpoint",
        endpoint,
    ]
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
            "tests/server/a2a_server.py",
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
