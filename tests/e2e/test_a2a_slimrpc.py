# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import pytest
from a2a.client import ClientFactory, minimal_agent_card
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import (
    Role,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)

from slima2a import setup_slim_client
from slima2a.client_transport import (
    ClientConfig as SRPCClientConfig,
    SRPCTransport,
    slimrpc_channel_factory,
)

from agntcy_app_sdk.semantic.a2a import ClientConfig as A2AClientConfig
from agntcy_app_sdk.semantic.a2a import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.client.config import SlimRpcConfig
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _RecordingInterceptor(ClientCallInterceptor):
    """Interceptor that records every call for assertion in tests."""

    def __init__(self):
        self.calls: list[str] = []

    async def before(self, args: BeforeArgs) -> None:
        self.calls.append(args.method)

    async def after(self, args: AfterArgs) -> None:
        pass


def _make_send_message(text: str = "how much is 10 USD in INR?") -> SendMessageRequest:
    """Build a simple user ``SendMessageRequest`` for the A2A client."""
    return SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))


async def _collect_text(client, request: SendMessageRequest) -> str:
    """Send *request* and return all text carried by the response events."""
    output = ""
    async for event in client.send_message(request):
        output += get_stream_response_text(event)
    return output


# ---------------------------------------------------------------------------
# test_client — basic point-to-point A2A request over SlimRPC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client(run_a2a_slimrpc_server):
    """Point-to-point A2A request over native SlimRPC (no BaseTransport)."""
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_client | SlimRPC | {endpoint} ---")

    # 1. Spawn SlimRPC server
    run_a2a_slimrpc_server(endpoint, name=agent_name)

    # 2. Setup SLIM client connection
    service, slim_local_app, local_name, conn_id = await setup_slim_client(
        namespace="default",
        group="default",
        name="test_client",
        slim_url=endpoint,
    )

    # 3. Create A2A client via upstream a2a-sdk ClientFactory + SRPCTransport
    client_config = SRPCClientConfig(
        supported_protocol_bindings=["slimrpc"],
        slimrpc_channel_factory=slimrpc_channel_factory(slim_local_app, conn_id),
    )
    client_factory = ClientFactory(client_config)
    client_factory.register("slimrpc", SRPCTransport.create)  # type: ignore[arg-type]

    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = client_factory.create(card=agent_card)

    # 4. Send message and validate response
    request = _make_send_message()
    output = await _collect_text(client, request)

    assert output, "Response was empty"
    assert "Hello from" in output, f"Expected 'Hello from' in response, got: {output}"
    print(f"Agent responded: {output}")

    print("=== test_client passed for SlimRPC ===\n")


# ---------------------------------------------------------------------------
# test_client_factory — A2AClientFactory with SDK ClientConfig over SlimRPC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_factory(run_a2a_slimrpc_server):
    """Point-to-point A2A request using A2AClientFactory + A2AClientConfig.

    Unlike test_client (which wires up slima2a primitives manually), this test
    exercises the SDK's own ClientConfig / A2AClientFactory abstraction with a
    pre-built (eager) slimrpc_channel_factory on the config.
    """
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_client_factory | SlimRPC | {endpoint} ---")

    # 1. Spawn SlimRPC server
    run_a2a_slimrpc_server(endpoint, name=agent_name)

    # 2. Setup SLIM client connection (low-level, needed for the channel factory)
    _service, slim_local_app, _local_name, conn_id = await setup_slim_client(
        namespace="default",
        group="default",
        name="test_client_factory",
        slim_url=endpoint,
    )

    # 3. Build SDK ClientConfig with eager slimrpc_channel_factory
    config = A2AClientConfig(
        slimrpc_channel_factory=slimrpc_channel_factory(slim_local_app, conn_id),
    )

    # Verify supported_protocol_bindings was auto-derived
    assert "slimrpc" in config.supported_protocol_bindings, (
        f"Expected 'slimrpc' in supported_protocol_bindings, "
        f"got: {config.supported_protocol_bindings}"
    )

    # 4. Create client via A2AClientFactory
    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.create(card=agent_card)

    # 5. Send message and validate response
    request = _make_send_message()
    output = await _collect_text(client, request)

    assert output, "Response was empty"
    assert "Hello from" in output, f"Expected 'Hello from' in response, got: {output}"
    print(f"Agent responded: {output}")

    print("=== test_client_factory passed for SlimRPC ===\n")


# ---------------------------------------------------------------------------
# test_client_factory_deferred — A2AClientFactory with deferred SlimRpcConfig
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_factory_deferred(run_a2a_slimrpc_server):
    """Point-to-point A2A request using deferred SlimRpcConfig.

    Unlike test_client_factory (which pre-builds the channel factory eagerly),
    this test passes only a SlimRpcConfig and lets the factory call
    setup_slim_client lazily during create().
    """
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_client_factory_deferred | SlimRPC | {endpoint} ---")

    # 1. Spawn SlimRPC server
    run_a2a_slimrpc_server(endpoint, name=agent_name)

    # 2. Build SDK ClientConfig with deferred SlimRpcConfig — no manual
    #    setup_slim_client call needed.
    config = A2AClientConfig(
        slimrpc_config=SlimRpcConfig(
            namespace="default",
            group="default",
            name="test_client_factory_deferred",
            slim_url=endpoint,
        ),
    )

    # Verify supported_protocol_bindings was auto-derived
    assert "slimrpc" in config.supported_protocol_bindings, (
        f"Expected 'slimrpc' in supported_protocol_bindings, "
        f"got: {config.supported_protocol_bindings}"
    )

    # 3. Create client via A2AClientFactory — factory handles async setup
    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.create(card=agent_card)

    # 4. Send message and validate response
    request = _make_send_message()
    output = await _collect_text(client, request)

    assert output, "Response was empty"
    assert "Hello from" in output, f"Expected 'Hello from' in response, got: {output}"
    print(f"Agent responded: {output}")

    print("=== test_client_factory_deferred passed for SlimRPC ===\n")


# ---------------------------------------------------------------------------
# test_task_status_events — streaming TaskStatusUpdateEvent lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_events(run_a2a_slimrpc_server):
    """Verify the client receives streaming TaskStatusUpdateEvent objects over SlimRPC.

    Uses deferred SlimRpcConfig (matching test_client_factory_deferred pattern).
    The HelloWorldStreamingAgentExecutor produces:
      1. An initial Task event
      2. N × TaskStatusUpdateEvent with state=TASK_STATE_WORKING (one per token)
      3. 1 × TaskStatusUpdateEvent with state=TASK_STATE_COMPLETED (terminal)
    """
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_task_status_events | SlimRPC | {endpoint} ---")

    # 1. Spawn SlimRPC server with streaming executor
    run_a2a_slimrpc_server(endpoint, name=agent_name, streaming=True)

    # 2. Build SDK ClientConfig with streaming enabled + deferred SlimRpcConfig
    config = A2AClientConfig(
        streaming=True,
        slimrpc_config=SlimRpcConfig(
            namespace="default",
            group="default",
            name="test_task_status_events",
            slim_url=endpoint,
        ),
    )

    # 3. Create client via A2AClientFactory
    #    Use a card with capabilities.streaming=True so BaseClient uses the
    #    streaming path (minimal_agent_card leaves streaming=None which
    #    upstream treats as "no streaming").
    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    agent_card.capabilities.streaming = True
    client = await factory.create(card=agent_card)

    # 4. Collect all events from the streaming response
    request = _make_send_message()
    events: list[StreamResponse] = []
    async for event in client.send_message(request):
        if event.HasField("message"):
            pytest.fail(
                f"Expected Task / status updates but got a bare Message: {event}"
            )
        events.append(event)

    print(f"Received {len(events)} events")

    # --- Assertion 1: multiple events received (not collapsed) ---
    assert len(events) >= 3, (
        f"Expected at least 3 events (initial + working + completed), got {len(events)}"
    )

    # --- Assertion 2: first event is the initial Task ---
    assert events[0].HasField("task"), "First event should contain a Task"

    # Separate status update events (skip the initial Task event)
    status_events = [e.status_update for e in events[1:] if e.HasField("status_update")]
    assert len(status_events) >= 2, (
        f"Expected at least 2 status updates (working + completed), got {len(status_events)}"
    )

    # --- Assertion 3: at least one working state ---
    working_events = [
        se for se in status_events if se.status.state == TaskState.TASK_STATE_WORKING
    ]
    assert len(working_events) >= 1, "Expected at least one working status update"

    # --- Assertion 4: exactly one completed state ---
    completed_events = [
        se for se in status_events if se.status.state == TaskState.TASK_STATE_COMPLETED
    ]
    assert len(completed_events) == 1, (
        f"Expected exactly 1 completed status update, got {len(completed_events)}"
    )

    # --- Assertion 5: last status event is the (terminal) completed one ---
    last_status = status_events[-1]
    assert last_status.status.state == TaskState.TASK_STATE_COMPLETED, (
        f"Last status should be completed, got "
        f"{TaskState.Name(last_status.status.state)}"
    )

    # --- Assertion 6: working events carry a message ---
    for we in working_events:
        assert we.status.HasField("message"), (
            "Working status events should carry a message with the streamed token"
        )

    print(
        "Status transitions: "
        f"{[TaskState.Name(se.status.state) for se in status_events]}"
    )
    print("=== test_task_status_events passed for SlimRPC ===\n")


# ---------------------------------------------------------------------------
# test_interceptor — verify interceptors fire over SlimRPC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interceptor(run_a2a_slimrpc_server):
    """Interceptor middleware should be invoked on send_message over SlimRPC.

    With a2a-sdk 1.x the interceptors are run by the upstream ``BaseClient``
    that wraps the transport, so ``SRPCTransport`` does not need to handle
    them.  This test covers that the interceptors run; it does not cover
    forwarding of the call context, which ``SRPCTransport`` ignores.
    """
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_interceptor | SlimRPC | {endpoint} ---")

    run_a2a_slimrpc_server(endpoint, name=agent_name)

    interceptor = _RecordingInterceptor()

    config = A2AClientConfig(
        slimrpc_config=SlimRpcConfig(
            namespace="default",
            group="default",
            name="test_interceptor",
            slim_url=endpoint,
        ),
    )

    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.create(card=agent_card, interceptors=[interceptor])

    request = _make_send_message()
    async for _event in client.send_message(request):
        pass

    # --- Core assertion: interceptor was called ---
    assert len(interceptor.calls) >= 1, (
        "Interceptor was never called for SlimRPC transport. "
        "Interceptors are likely being dropped by SRPCTransport."
    )

    method_names = interceptor.calls
    assert "send_message" in method_names or "send_message_streaming" in method_names, (
        f"Interceptor called with unexpected methods: {method_names}"
    )

    print(f"Interceptor called {len(interceptor.calls)} time(s): {method_names}")

    print("=== test_interceptor passed for SlimRPC ===\n")
