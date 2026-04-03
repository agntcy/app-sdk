# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest
from a2a.types import (
    Message,
    Role,
    SendMessageResponse,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)
from ioa_observe.sdk.tracing import session_start

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from tests.e2e.conftest import (
    TRANSPORT_CONFIGS,
    make_agent_card,
    make_message,
    make_send_request,
    make_streaming_send_request,
)

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# test_client — basic point-to-point A2A request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_a2a_server, transport):
    """Point-to-point A2A request over each transport."""
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_client | {transport} | {endpoint} ---")

    run_a2a_server(transport, endpoint)
    await asyncio.sleep(1)

    factory = AgntcyFactory(enable_tracing=True)

    if transport == "JSONRPC":
        # Native HTTP JSONRPC — no transport instance needed.
        # Use streaming=False because the test server uses a non-streaming
        # executor (HelloWorldAgentExecutor) which is incompatible with the
        # upstream BaseClient streaming protocol.
        session_start()

        client = await A2AClientFactory.connect(
            endpoint, config=ClientConfig(streaming=False)
        )
    else:
        transport_instance = factory.create_transport(
            transport, endpoint=endpoint, name="default/default/default"
        )

        session_start()

        config_kwargs = {}
        if transport == "SLIM":
            config_kwargs["slim_transport"] = transport_instance
        elif transport == "NATS":
            config_kwargs["nats_transport"] = transport_instance

        # The name must match what the server subscribes to.  The fixture
        # default name is "default/default/Hello_World_Agent_1.0.0" and the
        # server uses that as its subscription topic (via ``topic or name``).
        card = make_agent_card("default/default/Hello_World_Agent_1.0.0", transport)
        a2a = factory.a2a(ClientConfig(**config_kwargs))
        client = await a2a.create(card)

    assert client is not None, "Client was not created"
    print(f"Agent: {(await client.get_card()).name}")

    request = make_message()
    output = ""
    async for event in client.send_message(request):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    output += part.root.text
        else:
            task, _update = event
            if task.history:
                for msg in task.history:
                    if msg.role == Role.agent:
                        for part in msg.parts:
                            if isinstance(part.root, TextPart):
                                output += part.root.text

    assert output, "Response was empty"
    assert "Hello from" in output, f"Expected 'Hello from' in response, got: {output}"
    print(f"Agent responded: {output}")

    if transport != "JSONRPC" and transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_client passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_broadcast — fan-out A2A request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_broadcast(run_a2a_server, transport):
    """Fan-out A2A broadcast to multiple agents."""
    if transport == "A2A":
        pytest.skip("Broadcast not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Broadcast not applicable for JSONRPC transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_broadcast | {transport} | {endpoint} ---")

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    agent_names = [
        "default/default/agent1",
        "default/default/agent2",
        "default/default/agent3",
    ]
    for name in agent_names:
        run_a2a_server(transport, endpoint, name=name)

    # Allow extra time for all agents to subscribe (CI runners can be slow)
    await asyncio.sleep(5)

    config_kwargs = {}
    if transport == "SLIM":
        config_kwargs["slim_transport"] = transport_instance
    elif transport == "NATS":
        config_kwargs["nats_transport"] = transport_instance

    # Card points at the first agent — used for client negotiation only.
    # The invite protocol reaches all agents via their individual names.
    card = make_agent_card(agent_names[0], transport)
    a2a = factory.a2a(ClientConfig(**config_kwargs))
    client = await a2a.create(card)
    assert client is not None, "Client was not created"

    request = make_send_request()
    responses = await client.broadcast_message(
        request,
        recipients=agent_names,
    )

    print(f"Received {len(responses)} broadcast responses")
    assert len(responses) == 3, "Did not receive expected number of broadcast responses"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_broadcast passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_broadcast_streaming — fan-out with streaming responses
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_broadcast_streaming(run_a2a_server, transport):
    """Fan-out A2A broadcast with streaming responses."""
    if transport == "A2A":
        pytest.skip("Broadcast not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Broadcast not applicable for JSONRPC transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_broadcast_streaming | {transport} | {endpoint} ---")

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    agent_names = [
        "default/default/agent1",
        "default/default/agent2",
        "default/default/agent3",
    ]
    for name in agent_names:
        run_a2a_server(transport, endpoint, name=name)

    # Allow extra time for all agents to subscribe (CI runners can be slow)
    await asyncio.sleep(5)

    config_kwargs = {}
    if transport == "SLIM":
        config_kwargs["slim_transport"] = transport_instance
    elif transport == "NATS":
        config_kwargs["nats_transport"] = transport_instance

    # Card points at the first agent — used for client negotiation only.
    card = make_agent_card(agent_names[0], transport)
    a2a = factory.a2a(ClientConfig(**config_kwargs))
    client = await a2a.create(card)
    assert client is not None, "Client was not created"

    request = make_send_request()
    responses = []
    async for resp in client.broadcast_message_streaming(
        request,
        message_limit=3,
        recipients=agent_names,
    ):
        print(f"Streaming response: {resp}")
        responses.append(resp)

    print(f"Received {len(responses)} streaming responses")
    assert len(responses) == 3, "Did not receive expected number of broadcast responses"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_broadcast_streaming passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_groupchat — multi-party conversation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_groupchat(run_a2a_server, transport):
    """Multi-party group chat over SLIM transport."""
    if transport == "A2A":
        pytest.skip("Group chat not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Group chat not applicable for JSONRPC transport.")
    if transport == "NATS":
        pytest.skip("Group chat not applicable for NATS transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_groupchat | {transport} | {endpoint} ---")

    participants = ["default/default/foo", "default/default/bar"]
    for name in participants:
        run_a2a_server(transport, endpoint, name=name)

    await asyncio.sleep(3)

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    config_kwargs = {}
    if transport == "SLIM":
        config_kwargs["slim_transport"] = transport_instance
    elif transport == "NATS":
        config_kwargs["nats_transport"] = transport_instance

    card = make_agent_card(participants[0], transport)
    a2a = factory.a2a(ClientConfig(**config_kwargs))
    client = await a2a.create(card)
    assert client is not None, "Client was not created"

    request = make_send_request("This is a groupchat message")
    responses = await client.start_groupchat(
        init_message=request,
        group_channel="zoo",
        participants=participants,
        end_message="DELIVERED",
        timeout=30,
    )

    print(f"Received {len(responses)} group chat responses")
    assert len(responses) > 0, "No group chat responses received (possible timeout)"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_groupchat passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_groupchat_streaming — multi-party with streaming
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_groupchat_streaming(run_a2a_server, transport):
    """Multi-party streaming group chat over SLIM transport."""
    if transport == "A2A":
        pytest.skip("Group chat not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Group chat not applicable for JSONRPC transport.")
    if transport == "NATS":
        pytest.skip("Group chat not applicable for NATS transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_groupchat_streaming | {transport} | {endpoint} ---")

    participants = ["default/default/foo", "default/default/bar"]
    for name in participants:
        run_a2a_server(transport, endpoint, name=name)

    await asyncio.sleep(3)

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    config_kwargs = {}
    if transport == "SLIM":
        config_kwargs["slim_transport"] = transport_instance
    elif transport == "NATS":
        config_kwargs["nats_transport"] = transport_instance

    card = make_agent_card(participants[0], transport)
    a2a = factory.a2a(ClientConfig(**config_kwargs))
    client = await a2a.create(card)
    assert client is not None, "Client was not created"

    request = make_send_request("This is a groupchat message")
    messages = []
    async for message in client.start_streaming_groupchat(
        init_message=request,
        group_channel="zoo",
        participants=participants,
        end_message="DELIVERED",
        timeout=30,
    ):
        print(f"Streaming message: {message}")
        messages.append(message)

    assert len(messages) > 0, (
        "No streaming group chat messages received (possible timeout)"
    )

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_groupchat_streaming passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_task_status_events — streaming TaskStatusUpdateEvent lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_task_status_events(run_a2a_server, transport):
    """Verify the client receives streaming TaskStatusUpdateEvent objects.

    The HelloWorldStreamingAgentExecutor produces:
      1. An initial Task event
      2. N × TaskStatusUpdateEvent with state=working (one per token)
      3. 1 × TaskStatusUpdateEvent with state=completed, final=True
    """
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_task_status_events | {transport} | {endpoint} ---")

    run_a2a_server(transport, endpoint, streaming=True)
    await asyncio.sleep(1)

    transport_instance = None

    if transport == "JSONRPC":
        # Native HTTP JSONRPC — no transport instance needed.
        client = await A2AClientFactory.connect(
            endpoint, config=ClientConfig(streaming=True)
        )
    else:
        factory = AgntcyFactory(enable_tracing=True)
        transport_instance = factory.create_transport(
            transport, endpoint=endpoint, name="default/default/default"
        )

        config_kwargs: dict = {"streaming": True}
        if transport == "SLIM":
            config_kwargs["slim_transport"] = transport_instance
        elif transport == "NATS":
            config_kwargs["nats_transport"] = transport_instance

        card = make_agent_card(
            "default/default/Hello_World_Agent_1.0.0",
            transport,
            streaming=True,
        )
        a2a = factory.a2a(ClientConfig(**config_kwargs))
        client = await a2a.create(card)

    assert client is not None, "Client was not created"

    request = make_message()

    # Collect all events from the streaming response
    events: list[tuple[Task, TaskStatusUpdateEvent | None]] = []
    async for event in client.send_message(request):
        if isinstance(event, Message):
            # Streaming executor should produce Task events, not bare Messages
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

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_task_status_events passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_broadcast_task_status_events — fan-out streaming with status events
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_broadcast_task_status_events(run_a2a_server, transport):
    """Verify broadcast_message_streaming yields TaskStatusUpdateEvent objects.

    Starts 3 streaming agents, broadcasts a SendStreamingMessageRequest, and
    asserts that intermediate TaskStatusUpdateEvent/Task events are received
    alongside the 3 final SendMessageResponse messages.
    """
    if transport == "A2A":
        pytest.skip("Broadcast not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Broadcast not applicable for JSONRPC transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_broadcast_task_status_events | {transport} | {endpoint} ---")

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    agent_names = [
        "default/default/agent1",
        "default/default/agent2",
        "default/default/agent3",
    ]
    for name in agent_names:
        run_a2a_server(transport, endpoint, name=name, streaming=True)

    # Allow extra time for all agents to subscribe (CI runners can be slow)
    await asyncio.sleep(5)

    config_kwargs: dict = {"streaming": True}
    if transport == "SLIM":
        config_kwargs["slim_transport"] = transport_instance
    elif transport == "NATS":
        config_kwargs["nats_transport"] = transport_instance

    card = make_agent_card(agent_names[0], transport, streaming=True)
    a2a = factory.a2a(ClientConfig(**config_kwargs))
    client = await a2a.create(card)
    assert client is not None, "Client was not created"

    # Use SendStreamingMessageRequest so the server dispatches to
    # message/stream -> streaming executor which emits status events.
    request = make_streaming_send_request()

    import time
    from collections import defaultdict

    status_events_received: list[TaskStatusUpdateEvent] = []
    task_events_received: list[Task] = []
    final_responses: list[SendMessageResponse] = []
    all_events_ordered: list = []  # preserves arrival order for ordering checks

    start_time = time.monotonic()

    async for event in client.broadcast_message_streaming(
        request,
        recipients=agent_names,
    ):
        all_events_ordered.append(event)
        if isinstance(event, TaskStatusUpdateEvent):
            status_events_received.append(event)
        elif isinstance(event, Task):
            task_events_received.append(event)
        elif isinstance(event, SendMessageResponse):
            final_responses.append(event)

    elapsed = time.monotonic() - start_time

    total_events = (
        len(status_events_received) + len(task_events_received) + len(final_responses)
    )
    print(
        f"Received {len(status_events_received)} status events, "
        f"{len(task_events_received)} task events, "
        f"{len(final_responses)} final responses "
        f"({total_events} total, {elapsed:.1f}s)"
    )

    # --- Assertion 1: stream completed well under the timeout ---
    # A clean termination (all 3 agents responded) should be fast.
    # If we're near the 60s timeout, the stream likely timed out.
    assert elapsed < 30.0, (
        f"Stream took {elapsed:.1f}s — expected <30s; "
        "likely timed out instead of terminating cleanly"
    )

    # --- Assertion 2: multiple status events received (not collapsed) ---
    assert len(status_events_received) >= 3, (
        f"Expected at least 3 TaskStatusUpdateEvent (one per agent minimum), "
        f"got {len(status_events_received)}"
    )

    # --- Assertion 3: events came from 3 distinct agents ---
    # Each agent creates a unique context_id for its task, so distinct
    # context_ids prove events arrived from different agents.
    context_ids = {se.context_id for se in status_events_received}
    assert len(context_ids) == 3, (
        f"Expected events from 3 distinct agents (context_ids), "
        f"got {len(context_ids)}: {context_ids}"
    )

    # --- Assertion 4: all status events have correct kind ---
    for se in status_events_received:
        assert isinstance(se, TaskStatusUpdateEvent), (
            f"Expected TaskStatusUpdateEvent, got {type(se)}"
        )
        assert se.kind == "status-update", (
            f"Expected kind='status-update', got '{se.kind}'"
        )

    # --- Assertion 5: at least one working event per agent ---
    working_by_ctx: dict[str, list[TaskStatusUpdateEvent]] = defaultdict(list)
    for se in status_events_received:
        if se.status.state == TaskState.working:
            working_by_ctx[se.context_id].append(se)
    assert len(working_by_ctx) == 3, (
        f"Expected working events from all 3 agents, "
        f"got working events from {len(working_by_ctx)}: "
        f"{set(working_by_ctx.keys())}"
    )

    # --- Assertion 6: exactly one completed event per agent ---
    completed_by_ctx: dict[str, list[TaskStatusUpdateEvent]] = defaultdict(list)
    for se in status_events_received:
        if se.status.state == TaskState.completed:
            completed_by_ctx[se.context_id].append(se)
    assert len(completed_by_ctx) == 3, (
        f"Expected completed events from all 3 agents, "
        f"got completed events from {len(completed_by_ctx)}: "
        f"{set(completed_by_ctx.keys())}"
    )
    for ctx_id, completed_list in completed_by_ctx.items():
        assert len(completed_list) == 1, (
            f"Agent {ctx_id}: expected exactly 1 completed event, "
            f"got {len(completed_list)}"
        )

    # --- Assertion 7: working events have final=False ---
    all_working = [
        se for se in status_events_received if se.status.state == TaskState.working
    ]
    for we in all_working:
        assert we.final is False, (
            f"Working status events should have final=False, got final={we.final}"
        )

    # --- Assertion 8: completed events have final=True ---
    all_completed = [
        se for se in status_events_received if se.status.state == TaskState.completed
    ]
    for ce in all_completed:
        assert ce.final is True, (
            f"Completed status events should have final=True, got final={ce.final}"
        )

    # --- Assertion 9: working events carry a message (streamed token) ---
    for we in all_working:
        assert we.status.message is not None, (
            "Working status events should carry a message with the streamed token"
        )

    # --- Assertion 10: per-agent ordering — working before completed ---
    events_by_ctx: dict[str, list[TaskStatusUpdateEvent]] = defaultdict(list)
    for se in status_events_received:
        events_by_ctx[se.context_id].append(se)
    for ctx_id, agent_events in events_by_ctx.items():
        states = [e.status.state for e in agent_events]
        # Find the completed event index; everything before it must be working
        try:
            completed_idx = states.index(TaskState.completed)
        except ValueError:
            continue  # no completed event for this agent (caught by assertion 6)
        for i in range(completed_idx):
            assert states[i] == TaskState.working, (
                f"Agent {ctx_id}: expected working before completed, "
                f"but state[{i}]={states[i].value} (completed at index {completed_idx})"
            )

    # --- Assertion 11: exactly 3 finals total ---
    total_finals = len(final_responses) + len(all_completed)
    assert total_finals == 3, (
        f"Expected exactly 3 finals (one per agent), got {total_finals} "
        f"({len(final_responses)} SendMessageResponse + "
        f"{len(all_completed)} completed TaskStatusUpdateEvent)"
    )

    print("Status transitions by agent:")
    for ctx_id, agent_events in events_by_ctx.items():
        transitions = [e.status.state.value for e in agent_events]
        print(f"  {ctx_id[:8]}...: {transitions}")

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_broadcast_task_status_events passed for {transport} ===\n")
