# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from uuid import uuid4

import pytest
from a2a.client import ClientFactory, minimal_agent_card
from a2a.types import (
    AgentCapabilities,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
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


def _make_send_message(text: str = "how much is 10 USD in INR?") -> Message:
    """Build a simple A2A Message for the slima2a client."""
    return Message(
        role=Role.user,
        message_id=str(uuid4()),
        parts=[Part(root=TextPart(text=text))],
    )


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
    await asyncio.sleep(2)

    # 2. Setup SLIM client connection
    service, slim_local_app, local_name, conn_id = await setup_slim_client(
        namespace="default",
        group="default",
        name="test_client",
        slim_url=endpoint,
    )

    # 3. Create A2A client via upstream a2a-sdk ClientFactory + SRPCTransport
    client_config = SRPCClientConfig(
        supported_transports=["slimrpc"],
        slimrpc_channel_factory=slimrpc_channel_factory(slim_local_app, conn_id),
    )
    client_factory = ClientFactory(client_config)
    client_factory.register("slimrpc", SRPCTransport.create)  # type: ignore[arg-type]

    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = client_factory.create(card=agent_card)

    # 4. Send message and validate response
    request = _make_send_message()
    output = ""
    async for event in client.send_message(request=request):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    output += part.root.text
        else:
            task, update = event
            if task.status.state == "completed" and task.artifacts:
                for artifact in task.artifacts:
                    for part in artifact.parts:
                        if isinstance(part.root, TextPart):
                            output += part.root.text

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
    await asyncio.sleep(2)

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

    # Verify supported_transports was auto-derived
    assert "slimrpc" in config.supported_transports, (
        f"Expected 'slimrpc' in supported_transports, got: {config.supported_transports}"
    )

    # 4. Create client via A2AClientFactory
    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.create(card=agent_card)

    # 5. Send message and validate response
    request = _make_send_message()
    output = ""
    async for event in client.send_message(request=request):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    output += part.root.text
        else:
            task, update = event
            if task.status.state == "completed" and task.artifacts:
                for artifact in task.artifacts:
                    for part in artifact.parts:
                        if isinstance(part.root, TextPart):
                            output += part.root.text

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
    await asyncio.sleep(2)

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

    # Verify supported_transports was auto-derived
    assert "slimrpc" in config.supported_transports, (
        f"Expected 'slimrpc' in supported_transports, got: {config.supported_transports}"
    )

    # 3. Create client via A2AClientFactory — factory handles async setup
    factory = A2AClientFactory(config)
    agent_card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.create(card=agent_card)

    # 4. Send message and validate response
    request = _make_send_message()
    output = ""
    async for event in client.send_message(request=request):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    output += part.root.text
        else:
            task, update = event
            if task.status.state == "completed" and task.artifacts:
                for artifact in task.artifacts:
                    for part in artifact.parts:
                        if isinstance(part.root, TextPart):
                            output += part.root.text

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
      2. N × TaskStatusUpdateEvent with state=working (one per token)
      3. 1 × TaskStatusUpdateEvent with state=completed, final=True
    """
    endpoint = TRANSPORT_CONFIGS["SLIM"]
    agent_name = "default/default/Hello_World_Agent_1.0.0"

    print(f"\n--- test_task_status_events | SlimRPC | {endpoint} ---")

    # 1. Spawn SlimRPC server with streaming executor
    run_a2a_slimrpc_server(endpoint, name=agent_name, streaming=True)
    await asyncio.sleep(2)

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
    agent_card.capabilities = AgentCapabilities(streaming=True)
    client = await factory.create(card=agent_card)

    # 4. Collect all events from the streaming response
    request = _make_send_message()
    events: list[tuple[Task, TaskStatusUpdateEvent | None]] = []
    async for event in client.send_message(request=request):
        if isinstance(event, Message):
            pytest.fail(
                f"Expected (Task, update) tuples but got a bare Message: {event}"
            )
        events.append(event)

    print(f"Received {len(events)} events")

    # --- Assertion 1: multiple events received (not collapsed) ---
    assert len(events) >= 3, (
        f"Expected at least 3 events (initial + working + completed), got {len(events)}"
    )

    # --- Assertion 2: first event is the initial Task ---
    first_task, first_update = events[0]
    assert isinstance(first_task, Task), "First event should contain a Task"
    assert first_update is None, "First event update should be None (initial Task)"

    # Separate status update events (skip the initial Task event)
    status_events = [update for _, update in events[1:] if update is not None]
    assert len(status_events) >= 2, (
        f"Expected at least 2 status updates (working + completed), got {len(status_events)}"
    )

    # --- Assertion 3: all status updates have correct kind ---
    for se in status_events:
        assert isinstance(se, TaskStatusUpdateEvent), (
            f"Expected TaskStatusUpdateEvent, got {type(se)}"
        )
        assert se.kind == "status-update", (
            f"Expected kind='status-update', got '{se.kind}'"
        )

    # --- Assertion 4: at least one working state ---
    working_events = [
        se for se in status_events if se.status.state == TaskState.working
    ]
    assert len(working_events) >= 1, "Expected at least one working status update"

    # --- Assertion 5: exactly one completed state ---
    completed_events = [
        se for se in status_events if se.status.state == TaskState.completed
    ]
    assert len(completed_events) == 1, (
        f"Expected exactly 1 completed status update, got {len(completed_events)}"
    )

    # --- Assertion 6: last status event is completed + final ---
    last_status = status_events[-1]
    assert last_status.status.state == TaskState.completed, (
        f"Last status should be completed, got {last_status.status.state}"
    )
    assert last_status.final is True, "Last status event should have final=True"

    # --- Assertion 7: working events are not final ---
    for we in working_events:
        assert we.final is False, (
            f"Working status events should have final=False, got final={we.final}"
        )

    # --- Assertion 8: working events carry a message ---
    for we in working_events:
        assert we.status.message is not None, (
            "Working status events should carry a message with the streamed token"
        )

    print(f"Status transitions: {[se.status.state.value for se in status_events]}")
    print("=== test_task_status_events passed for SlimRPC ===\n")
