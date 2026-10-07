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


def _server_log_tail(log_path, limit=2000):
    """Return the last *limit* characters of a server log, if it can be read."""
    try:
        with open(log_path, errors="replace") as fh:
            return fh.read()[-limit:]
    except OSError:
        return ""


def _probe_slimrpc(proc, log_path, endpoint, agent_name, secret, streaming, timeout):
    """Block until *agent_name* answers a real SlimRPC ``send_message``.

    Runs in a helper thread with its own event loop because this is called
    from synchronous fixtures, sometimes while the test loop is already
    running.  The probe connection is closed before return, and SLIM globals
    are cleared so the test builds its own client afterwards.

    Raises ``TimeoutError`` (with the server log tail) if the process exits
    or no greeting arrives within *timeout* seconds.
    """
    import asyncio
    import threading

    from a2a.client import ClientFactory, minimal_agent_card
    from a2a.helpers import get_stream_response_text
    from slima2a import setup_slim_client
    from slima2a.client_transport import (
        ClientConfig as SRPCClientConfig,
    )
    from slima2a.client_transport import (
        SRPCTransport,
        slimrpc_channel_factory,
    )

    holder: dict = {}

    async def _roundtrip(client) -> str:
        request = SendMessageRequest(
            message=new_text_message("ready?", role=Role.ROLE_USER)
        )
        output = ""
        async for event in client.send_message(request):
            output += get_stream_response_text(event)
        return output

    async def _attempt() -> None:
        deadline = time.monotonic() + timeout
        last_error: object = "no attempt yet"
        service, _app, _local_name, conn_id = await setup_slim_client(
            namespace="default",
            group="default",
            name="e2e-ready-probe",
            slim_url=endpoint,
            secret=secret,
            log_level="error",
        )
        try:
            card = minimal_agent_card(agent_name, ["slimrpc"])
            if streaming:
                card.capabilities.streaming = True
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                try:
                    client_config = SRPCClientConfig(
                        supported_protocol_bindings=["slimrpc"],
                        slimrpc_channel_factory=slimrpc_channel_factory(
                            _app, conn_id
                        ),
                    )
                    client_factory = ClientFactory(client_config)
                    client_factory.register("slimrpc", SRPCTransport.create)  # type: ignore[arg-type]
                    client = client_factory.create(card=card)
                    output = await asyncio.wait_for(_roundtrip(client), timeout=5)
                    if "Hello" in output:
                        return
                    last_error = f"unexpected response: {output!r}"
                except Exception as exc:
                    last_error = exc
                await asyncio.sleep(0.3)
        finally:
            try:
                service.disconnect(conn_id)
            except Exception:
                pass
        raise TimeoutError(
            f"SlimRPC server {agent_name!r} did not answer within {timeout}s "
            f"(exit code: {proc.poll()}). Last error: {last_error}\n"
            f"Server output:\n{_server_log_tail(log_path)}"
        )

    def _run() -> None:
        try:
            asyncio.run(_attempt())
        except Exception as exc:
            holder["error"] = exc

    thread = threading.Thread(target=_run, name="slimrpc-ready-probe", daemon=True)
    thread.start()
    # The attempt enforces *timeout* itself and then returns.
    thread.join(timeout + 15)
    try:
        if thread.is_alive():
            raise TimeoutError(
                f"SlimRPC readiness probe for {agent_name!r} is still running "
                f"after {timeout}s (exit code: {proc.poll()}).\n"
                f"Server output:\n{_server_log_tail(log_path)}"
            )
        if "error" in holder:
            raise holder["error"]
    finally:
        # Drop the probe's event-loop pin and connections. Do not shut the
        # SLIM runtime down: shutdown_blocking leaves the datapath closed and
        # the test cannot initialize a client afterwards.
        _reset_slim_globals()


def _spawn_server(
    procs,
    script,
    transport,
    endpoint,
    extra_args=None,
    slimrpc_probe=None,
    ready_timeout=60,
):
    """Launch a test server subprocess and track it for cleanup.

    If *slimrpc_probe* is given, block until a SlimRPC ``send_message`` to
    that agent returns a greeting.  Otherwise sleep one second, as before.

    *slimrpc_probe* keys: ``agent_name``, ``secret``, and optional
    ``streaming`` (default False).  The dataplane URL is *endpoint*.
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

    if slimrpc_probe is None:
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
        _probe_slimrpc(
            proc,
            log_path,
            endpoint,
            slimrpc_probe["agent_name"],
            slimrpc_probe["secret"],
            slimrpc_probe.get("streaming", False),
            ready_timeout,
        )
    finally:
        if proc.poll() is None:
            os.unlink(log_path)
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
        # Same placeholder the card-bootstrap server uses when
        # SLIM_SHARED_SECRET is unset.  Not a production credential.
        slim_secret = os.environ.get(
            "SLIM_SHARED_SECRET",
            "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS",
        )
        proc = _spawn_server(
            procs,
            "tests/server/a2a_card_bootstrap_server.py",
            transport,
            endpoint,
            extra_args=extra_args,
            slimrpc_probe=(
                {"agent_name": name, "secret": slim_secret}
                if transport == "SLIMRPC"
                else None
            ),
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
        # The dedicated SlimRPC test server hardcodes this placeholder.
        # Read it from the server config so the probe uses the same value.
        from tests.server.a2a_slimrpc_server import _build_a2a_slimrpc_config

        secret = _build_a2a_slimrpc_config(
            name=name, endpoint=endpoint, streaming=streaming
        ).connection.shared_secret
        return _spawn_server(
            procs,
            "tests/server/a2a_slimrpc_server.py",
            transport=None,
            endpoint=endpoint,
            extra_args=extra_args,
            slimrpc_probe={
                "agent_name": name,
                "secret": secret,
                "streaming": streaming,
            },
        )

    yield _run
    _cleanup_procs(procs)
    _reset_slim_globals()
