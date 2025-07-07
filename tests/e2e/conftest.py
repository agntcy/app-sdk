# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

import subprocess
import os
import signal
import time
import pytest

TRANSPORT_CONFIGS = {
    "A2A": "http://localhost:9999",
    "NATS": "localhost:4222",
    "SLIM": "http://localhost:46357",
}


@pytest.fixture
def run_server():
    procs = []

    def _run(transport, endpoint):
        cmd = [
            "uv",
            "run",
            "python",
            "tests/server/__server__.py",
            "--transport",
            transport,
            "--endpoint",
            endpoint,
        ]

        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)

        procs.append(proc)
        time.sleep(1)
        return proc

    yield _run

    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
