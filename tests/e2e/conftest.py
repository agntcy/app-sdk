# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import os
import signal
import socket
import subprocess
import time
import uuid
from typing import Any

import pytest
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    MessageSendParams,
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

# Map CLI/test transport labels → InterfaceTransport preferred_transport values
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
    """Build a simple A2A Message for ``client.send_message()``."""
    return Message(
        role="user",
        parts=[{"type": "text", "text": text}],
        messageId=str(uuid.uuid4()),
    )


def make_send_request(text: str = "how much is 10 USD in INR?") -> SendMessageRequest:
    """Build a simple A2A SendMessageRequest (for broadcast/groupchat)."""
    payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": text}],
            "messageId": str(uuid.uuid4()),
        },
    }
    return SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(**payload))


def make_agent_card(
    name: str,
    transport_type: str = "JSONRPC",
    http_port: int = 9999,
    streaming: bool = False,
) -> AgentCard:
    """Build a single AgentCard that declares all transports.

    The card lists SLIM, NATS, HTTP, and SlimRPC in ``additional_interfaces``
    and sets ``preferredTransport`` based on *transport_type*.  Both client and
    server can share this card — the only thing that varies per test is
    the *name* (the agent's routable identity, stamped into SLIM/NATS
    interface URLs) and which transport is preferred.

    Args:
        name: The agent's routable identity, stamped into SLIM/NATS
            interface URLs and used as the card ``name``.
        transport_type: ``"SLIM"``, ``"NATS"``, ``"JSONRPC"``, or
            ``"SLIMRPC"`` — sets ``preferredTransport`` so the client
            negotiation picks this transport first.
        http_port: Port for the JSONRPC interface (default 9999).
        streaming: If ``True``, set ``capabilities.streaming = True`` on
            the card so the upstream ``BaseClient`` takes the streaming
            code path.
    """
    preferred = PREFERRED_TRANSPORT[transport_type]

    # card.url is set per the preferred transport so that negotiation
    # finds the right entry in {preferred_transport: card.url}
    if transport_type == "SLIM":
        url = f"{SLIM_ENDPOINT}/{name}"
    elif transport_type == "NATS":
        url = f"{NATS_ENDPOINT}/{name}"
    elif transport_type == "SLIMRPC":
        url = f"{SLIM_ENDPOINT}/{name}"
    else:
        url = f"http://localhost:{http_port}/"

    return AgentCard(
        name=name,
        description="Test agent",
        url=url,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True)
        if streaming
        else AgentCapabilities(),
        skills=[],
        preferredTransport=preferred,
        additional_interfaces=[
            AgentInterface(
                transport=InterfaceTransport.SLIM_PATTERNS,
                url=f"{SLIM_ENDPOINT}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.NATS_PATTERNS,
                url=f"{NATS_ENDPOINT}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.JSONRPC,
                url=f"http://0.0.0.0:{http_port}",
            ),
            AgentInterface(
                transport=InterfaceTransport.SLIM_RPC,
                url=f"{SLIM_ENDPOINT}/{name}",
            ),
        ],
    )


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
        )

    yield _run
    _cleanup_procs(procs)
    _reset_slim_globals()
