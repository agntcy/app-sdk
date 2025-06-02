import subprocess
import os
import signal
import time
import pytest

# Define your transport configurations
TRANSPORT_CONFIGS = {
    # "A2A": None,  # Default transport (e.g., HTTP, no endpoint)
    "NATS": "localhost:4222",
    # "AGP": "http://localhost:46357"
}


@pytest.fixture(params=list(TRANSPORT_CONFIGS.items()), ids=lambda val: val[0])
def run_server(request):
    print(
        f"Running server with transport: {request.param[0]}, endpoint: {request.param[1]}"
    )
    transport, endpoint = request.param
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
    time.sleep(1)  # Consider using a health check or wait-for-port later
    yield proc
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
