# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
import signal
import socket
import subprocess
import tempfile
import time

import pytest
from a2a.helpers import new_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    Role,
    SendMessageRequest,
)

from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

TRANSPORT_CONFIGS = {
    "NATS": "localhost:4222",
    "SLIM": "http://localhost:46357",
    "JSONRPC": "http://localhost:9999",
}

# Well-known test-service endpoints (must match docker-compose)
SLIM_ENDPOINT = "slim://localhost:46357"
NATS_ENDPOINT = "nats://localhost:4222"

# Map CLI/test transport labels → InterfaceTransport protocol_binding values
PREFERRED_TRANSPORT: dict[str, str] = {
    "SLIM": InterfaceTransport.SLIM_PATTERNS,
    "NATS": InterfaceTransport.NATS_PATTERNS,
    "JSONRPC": InterfaceTransport.JSONRPC,
    "SLIMRPC": InterfaceTransport.SLIM_RPC,
}


# ---------------------------------------------------------------------------
# Shared A2A message / card helpers
# ---------------------------------------------------------------------------


def make_message(text: str = "how much is 10 USD in INR?") -> Message:
    """Build a simple user ``Message`` (a2a-sdk 1.x protobuf type)."""
    return new_text_message(text, role=Role.ROLE_USER)


def make_send_request(text: str = "how much is 10 USD in INR?") -> SendMessageRequest:
    """Build a ``SendMessageRequest`` for ``client.send_message()`` and
    the broadcast / groupchat helpers."""
    return SendMessageRequest(message=make_message(text))


def make_streaming_send_request(
    text: str = "how much is 10 USD in INR?",
) -> SendMessageRequest:
    """Alias of :func:`make_send_request`.

    a2a-sdk 1.x has no separate streaming request type: whether the call
    streams is decided by the client method / card capabilities.  Kept so
    tests that exercise streaming broadcast read naturally.
    """
    return make_send_request(text)


def make_agent_card(
    name: str,
    transport_type: str = "JSONRPC",
    http_port: int = 9999,
    streaming: bool = False,
) -> AgentCard:
    """Build a single AgentCard that declares all transports.

    The card lists SLIM, NATS, HTTP, and SlimRPC in ``supported_interfaces``
    and orders them so that *transport_type* comes first (list order =
    server preference).  Both client and server can share this card — the
    only thing that varies per test is the *name* (the agent's routable
    identity, stamped into SLIM/NATS interface URLs) and which transport is
    preferred.

    Args:
        name: The agent's routable identity, stamped into SLIM/NATS
            interface URLs and used as the card ``name``.
        transport_type: ``"SLIM"``, ``"NATS"``, ``"JSONRPC"``, or
            ``"SLIMRPC"`` — moved to the front of ``supported_interfaces``
            so the client negotiation picks this transport first.
        http_port: Port for the JSONRPC interface (default 9999).
        streaming: If ``True``, set ``capabilities.streaming = True`` on
            the card so the upstream ``BaseClient`` takes the streaming
            code path.
    """
    preferred = PREFERRED_TRANSPORT[transport_type]

    interfaces = [
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_PATTERNS,
            url=f"{SLIM_ENDPOINT}/{name}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.NATS_PATTERNS,
            url=f"{NATS_ENDPOINT}/{name}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.JSONRPC,
            url=f"http://0.0.0.0:{http_port}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_RPC,
            url=f"{SLIM_ENDPOINT}/{name}",
        ),
    ]
    # Stable sort: the preferred transport first, others keep their order.
    interfaces.sort(key=lambda i: i.protocol_binding != preferred)

    return AgentCard(
        name=name,
        description="Test agent",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True)
        if streaming
        else AgentCapabilities(),
        skills=[],
        supported_interfaces=interfaces,
    )


# ---------------------------------------------------------------------------
# Shared subprocess helper
# ---------------------------------------------------------------------------


def _wait_for_log_marker(proc, log_path, marker, timeout):
    """Block until *marker* shows up in the server's log file.

    Raises ``TimeoutError`` (with the log tail) if the server exits first or
    the marker does not appear within *timeout* seconds.
    """
    deadline = time.time() + timeout
    text = ""
    while time.time() < deadline:
        with open(log_path, errors="replace") as fh:
            text = fh.read()
        if marker in text:
            return
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise TimeoutError(
        f"Server did not log {marker!r} within {timeout}s "
        f"(exit code: {proc.poll()}). Last output:\n{text[-2000:]}"
    )


def _spawn_server(
    procs,
    script,
    transport,
    endpoint,
    extra_args=None,
    ready_marker=None,
    ready_timeout=60,
):
    """Launch a test server subprocess and track it for cleanup.

    If *ready_marker* is given, the server's output goes to a temp log file and
    this call blocks until that text appears (the server is then subscribed on
    SLIM).  Without it we just sleep one second, as before.
    """
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

    if ready_marker is None:
        proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
        procs.append(proc)
        time.sleep(1)
        return proc

    log_fd, log_path = tempfile.mkstemp(prefix="e2e-server-", suffix=".log")
    with os.fdopen(log_fd, "w") as log_file:
        proc = subprocess.Popen(
            cmd,
            preexec_fn=os.setsid,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    procs.append(proc)
    try:
        _wait_for_log_marker(proc, log_path, ready_marker, ready_timeout)
    finally:
        # Keep the log only if something went wrong (the exception shows its tail).
        if proc.poll() is None:
            os.unlink(log_path)
    # The marker is logged just before the subscription reaches the dataplane.
    time.sleep(0.5)
    return proc


def _wait_for_port(host, port, timeout=30):
    """Block until a TCP port is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    raise TimeoutError(f"Port {host}:{port} not ready after {timeout}s")


def _cleanup_procs(procs, grace=15):
    """Terminate all tracked subprocesses and wait until they are gone.

    Waiting matters: a server that is still shutting down keeps its SLIM
    subscription, so the next test would talk to a dying server (or race it).
    """
    for proc in procs:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    for proc in procs:
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.wait()


def _reset_slim_globals():
    """Reset global SLIM state so the next test gets a fresh instance.

    slim_bindings caches the event loop set via ``uniffi_set_event_loop``.
    The SDK's ``get_or_create_slim_instance`` caches service/app/connection
    globals.  Both must be cleared between tests that run on separate
    asyncio event loops (pytest-asyncio creates a new loop per test).
    """
    import slim_bindings
    from agntcy_app_sdk.transport.slim import common as slim_common

    # Disconnect ALL connections from SLIM service.
    # Tests may create several connections (the global singleton connection
    # at "http://…:46357", a dedicated slimrpc connection at
    # "http://…:46357/", and ones the SDK opens internally).  Their ids are not
    # all stored anywhere (and looking them up by endpoint misses some, which
    # makes the next SLIM test fail with "connection not found"), so we
    # brute-force disconnect ids 0..9.  SLIM 2.x logs a harmless
    # "connection unknown" ERROR line for every id that is not open; pytest
    # hides that output unless a test fails.
    try:
        service = slim_bindings.get_global_service()
        for conn_id in range(10):
            try:
                service.disconnect(conn_id)
            except Exception:
                pass
        # Also disconnect our cached connection if it falls outside 0..9
        if (
            slim_common.global_connection_id is not None
            and slim_common.global_connection_id >= 10
        ):
            try:
                service.disconnect(slim_common.global_connection_id)
            except Exception:
                pass
    except Exception:
        pass  # Ignore errors during cleanup

    # Clear the cached event loop so subsequent calls fall back to
    # asyncio.get_running_loop() and pick up the new test's loop.
    # slim-bindings 2.x keeps one loop global per UniFFI namespace
    # (core + slimrpc); clear both.
    slim_bindings._slim_bindings.slim_bindings._UNIFFI_GLOBAL_EVENT_LOOP = None
    slim_bindings._slim_bindings.slim_rpc._UNIFFI_GLOBAL_EVENT_LOOP = None

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
        streaming=False,
    ):
        extra_args = [
            "--name",
            name,
            "--topic",
            topic,
            "--version",
            version,
        ]
        if streaming:
            extra_args.append("--streaming")
        proc = _spawn_server(
            procs,
            "tests/server/a2a_starlette_server.py",
            transport,
            endpoint,
            extra_args=extra_args,
        )
        # For JSONRPC (HTTP), wait until the server is accepting connections
        if transport == "JSONRPC":
            from urllib.parse import urlparse

            parsed = urlparse(endpoint)
            _wait_for_port(parsed.hostname or "localhost", parsed.port or 9999)
        return proc

    yield _run
    _cleanup_procs(procs)


@pytest.fixture
def run_card_bootstrap_server():
    """Spawn an A2A server that uses add_a2a_card() for bootstrap."""
    procs = []

    def _run(
        transport,
        endpoint,
        version="1.0.0",
        name="default/default/Hello_World_Agent_1.0.0",
        port=9999,
    ):
        extra_args = [
            "--name",
            name,
            "--version",
            version,
            "--port",
            str(port),
        ]
        proc = _spawn_server(
            procs,
            "tests/server/a2a_card_bootstrap_server.py",
            transport,
            endpoint,
            extra_args=extra_args,
            # The SlimRPC server logs this once it subscribes on SLIM.
            ready_marker="Subscribing base_name" if transport == "SLIMRPC" else None,
        )
        # For JSONRPC (HTTP), wait until the server is accepting connections
        if transport == "JSONRPC":
            from urllib.parse import urlparse

            parsed = urlparse(endpoint)
            _wait_for_port(parsed.hostname or "localhost", parsed.port or port)
        return proc

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
        proc = _spawn_server(
            procs,
            "tests/server/fast_mcp_server.py",
            transport,
            endpoint,
            extra_args=["--name", name],
        )
        # FastMCP starts an HTTP server on port 8081; wait for it to be ready
        _wait_for_port("localhost", 8081)
        return proc

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
        streaming=False,
    ):
        extra_args = [
            "--name",
            name,
            "--version",
            version,
        ]
        if streaming:
            extra_args.append("--streaming")
        return _spawn_server(
            procs,
            "tests/server/a2a_slimrpc_server.py",
            transport=None,
            endpoint=endpoint,
            extra_args=extra_args,
            # Logged by the SlimRPC server once it subscribes on SLIM.
            ready_marker="Subscribing base_name",
        )

    yield _run
    _cleanup_procs(procs)
    _reset_slim_globals()
