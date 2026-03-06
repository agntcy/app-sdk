# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for ``serve_card()`` card-driven bootstrap.

These tests prove that an A2A server bootstrapped via ``session.serve_card()``
actually works against real SLIM, NATS, and HTTP services — the same code path
that the unit tests cover, but exercised end-to-end with real transports.
"""

import asyncio
from unittest.mock import patch

import pytest
from a2a.types import (
    Message,
    Role,
    TextPart,
)
from ioa_observe.sdk.tracing import session_start

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import ServeCardPlan
from tests.e2e.conftest import (
    TRANSPORT_CONFIGS,
    make_agent_card,
    make_message,
    make_send_request,
)

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# test_client — basic point-to-point A2A request via serve_card() server
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_card_bootstrap_server, transport):
    """Point-to-point A2A request to a serve_card()-bootstrapped server."""
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_client (card_bootstrap) | {transport} | {endpoint} ---")

    run_card_bootstrap_server(transport, endpoint)
    await asyncio.sleep(1)

    factory = AgntcyFactory(enable_tracing=True)

    if transport == "JSONRPC":
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
        # server uses that as its subscription topic.
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

    if transport != "JSONRPC":
        await transport_instance.close()

    print(f"=== ✅ test_client (card_bootstrap) passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_broadcast — fan-out A2A request via serve_card() servers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_broadcast(run_card_bootstrap_server, transport):
    """Fan-out A2A broadcast to multiple serve_card()-bootstrapped agents."""
    if transport == "A2A":
        pytest.skip("Broadcast not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Broadcast not applicable for JSONRPC transport.")

    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_broadcast (card_bootstrap) | {transport} | {endpoint} ---")

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    agent_names = [
        "default/default/agent1",
        "default/default/agent2",
        "default/default/agent3",
    ]
    # Each subprocess starts ALL transports (including HTTP), so each needs a
    # unique port to avoid bind conflicts.
    for i, name in enumerate(agent_names):
        run_card_bootstrap_server(transport, endpoint, name=name, port=10001 + i)

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
    responses = await client.broadcast_message(
        request,
        recipients=agent_names,
    )

    print(f"Received {len(responses)} broadcast responses")
    assert len(responses) == 3, "Did not receive expected number of broadcast responses"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_broadcast (card_bootstrap) passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_dry_run — in-process plan validation (no Docker required)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run():
    """``serve_card(dry_run=True)`` returns a correct plan without starting anything."""
    factory = AgntcyFactory(enable_tracing=True)
    agent_card = make_agent_card("dry_run_topic")

    # Patch SLIM_SHARED_SECRET so serve_card() doesn't raise for SLIM
    env_patch = {"SLIM_SHARED_SECRET": "test-secret-for-dry-run-only"}
    with patch.dict("os.environ", env_patch):
        session = factory.create_app_session(max_sessions=10)
        plan = await session.serve_card(
            agent_card=agent_card,
            request_handler=None,  # Not needed for dry-run
            factory=factory,
            dry_run=True,
        )

    assert isinstance(plan, ServeCardPlan)
    assert (
        len(plan.containers) == 3
    ), f"Expected 3 plan entries (SLIM, NATS, JSONRPC), got {len(plan.containers)}"

    # Verify transport types in the plan
    transport_types = [entry["transport"] for entry in plan.containers]
    assert (
        "slimpatterns" in transport_types
    ), f"Missing slimpatterns in {transport_types}"
    assert (
        "natspatterns" in transport_types
    ), f"Missing natspatterns in {transport_types}"
    assert "jsonrpc" in transport_types, f"Missing jsonrpc in {transport_types}"

    # Verify no containers were actually started
    assert (
        len(session.app_containers) == 0
    ), f"dry_run=True should not create containers, found {len(session.app_containers)}"

    print("=== ✅ test_dry_run passed ===")
