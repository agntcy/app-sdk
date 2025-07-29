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
def run_server():
    procs = []

    def _run(transport, endpoint, version="1.0.0"):
        cmd = [
            "uv",
            "run",
            "python",
            "tests/server/__server__.py",
            "--transport",
            transport,
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
