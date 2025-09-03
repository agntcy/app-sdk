# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import subprocess
import os
import signal
import time
import pytest

TRANSPORT_CONFIGS = {
    # "A2A": "http://localhost:9999",
    "NATS": "localhost:4222",
    "SLIM": "http://localhost:46357",
}


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
        cmd = [
            "uv",
            "run",
            "python",
            "tests/server/a2a_server.py",
            "--transport",
            transport,
            "--name",
            name,
            "--topic",
            topic,
            "--endpoint",
            endpoint,
            "--version",
            version,
        ]

        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)

        procs.append(proc)
        time.sleep(1)
        return proc

    yield _run

    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)


@pytest.fixture
def run_mcp_server():
    procs = []

    def _run(transport, endpoint, name="default/default/mcp"):
        cmd = [
            "uv",
            "run",
            "python",
            "tests/server/mcp_server.py",
            "--transport",
            transport,
            "--endpoint",
            endpoint,
            "--name",
            name,
        ]

        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)

        procs.append(proc)
        time.sleep(1)
        return proc

    yield _run

    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)


@pytest.fixture
def run_fast_mcp_server():
    procs = []

    def _run(transport, endpoint, name="default/default/fastmcp"):
        cmd = [
            "uv",
            "run",
            "python",
            "tests/server/fast_mcp_server.py",
            "--transport",
            transport,
            "--endpoint",
            endpoint,
            "--name",
            name,
        ]

        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)

        procs.append(proc)
        time.sleep(1)
        return proc

    yield _run

    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
