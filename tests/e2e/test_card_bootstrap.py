# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for ``add_a2a_card()`` card-driven bootstrap.

These tests prove that an A2A server bootstrapped via ``session.add_a2a_card()``
actually works against real SLIM, NATS, SlimRPC, and HTTP services — the same
code path that the unit tests cover, but exercised end-to-end with real
transports.
"""

import asyncio
import os
from unittest.mock import patch

import pytest
from a2a.types import (
    Message,
    Role,
    TextPart,
)
from ioa_observe.sdk.tracing import session_start

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig, SlimRpcConfig
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import ServeCardPlan
from tests.e2e.conftest import (
    TRANSPORT_CONFIGS,
    make_agent_card,
    make_message,
    make_send_request,
)

pytest_plugins = "pytest_asyncio"

# Card-bootstrap tests exercise all four transports including SLIMRPC.
# The base TRANSPORT_CONFIGS only has NATS/SLIM/JSONRPC (used by starlette,
# MCP, etc.), so we extend it here.
CARD_BOOTSTRAP_TRANSPORT_CONFIGS = {
    **TRANSPORT_CONFIGS,
    "SLIMRPC": "http://localhost:46357",
}


# ---------------------------------------------------------------------------
# test_client — basic point-to-point A2A request via add_a2a_card() server
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(CARD_BOOTSTRAP_TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_card_bootstrap_server, transport):
    """Point-to-point A2A request to an add_a2a_card()-bootstrapped server."""
    endpoint = CARD_BOOTSTRAP_TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_client (card_bootstrap) | {transport} | {endpoint} ---")

    if transport == "SLIMRPC":
        # add_a2a_card() now starts slimrpc on a dedicated SLIM connection,
        # so it coexists with slimpatterns on the same server process.
        run_card_bootstrap_server(transport, endpoint)
        await asyncio.sleep(2)
    else:
        run_card_bootstrap_server(transport, endpoint)
        await asyncio.sleep(1)

    factory = AgntcyFactory(enable_tracing=True)

    if transport == "JSONRPC":
        session_start()

        client = await A2AClientFactory.connect(
            endpoint, config=ClientConfig(streaming=False)
        )
    elif transport == "SLIMRPC":
        session_start()

        # The add_a2a_card() server reads SLIM_SHARED_SECRET from the
        # environment — match that here so client and server agree.
        slim_secret = os.environ.get(
            "SLIM_SHARED_SECRET",
            "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS",
        )
        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="default",
                group="default",
                name="test_client",
                slim_url="http://localhost:46357",
                secret=slim_secret,
            ),
        )
        card = make_agent_card("default/default/Hello_World_Agent_1.0.0", "SLIMRPC")
        a2a = factory.a2a(config)
        client = await a2a.create(card)
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

    if transport not in ("JSONRPC", "SLIMRPC"):
        await transport_instance.close()

    print(f"=== ✅ test_client (card_bootstrap) passed for {transport} ===\n")


# ---------------------------------------------------------------------------
# test_broadcast — fan-out A2A request via add_a2a_card() servers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(CARD_BOOTSTRAP_TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_broadcast(run_card_bootstrap_server, transport):
    """Fan-out A2A broadcast to multiple add_a2a_card()-bootstrapped agents."""
    if transport == "A2A":
        pytest.skip("Broadcast not applicable for raw A2A transport.")
    if transport == "JSONRPC":
        pytest.skip("Broadcast not applicable for JSONRPC transport.")
    if transport == "SLIMRPC":
        pytest.skip("SLIMRPC is point-to-point only; broadcast not supported.")

    endpoint = CARD_BOOTSTRAP_TRANSPORT_CONFIGS[transport]
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
    """``add_a2a_card().dry_run()`` returns a correct plan without starting anything."""
    factory = AgntcyFactory(enable_tracing=True)
    agent_card = make_agent_card("dry_run_topic")

    # Patch SLIM_SHARED_SECRET so CardBuilder doesn't raise for SLIM
    env_patch = {"SLIM_SHARED_SECRET": "test-secret-for-dry-run-only"}
    with patch.dict("os.environ", env_patch):
        session = factory.create_app_session(max_sessions=10)
        plan = await (
            session.add_a2a_card(agent_card, None).with_factory(factory).dry_run()
        )

    assert isinstance(plan, ServeCardPlan)
    assert (
        len(plan.containers) == 4
    ), f"Expected 4 plan entries (SLIMRPC, SLIM, NATS, JSONRPC), got {len(plan.containers)}"

    # Verify transport types in the plan
    transport_types = [entry["transport"] for entry in plan.containers]
    assert (
        "slimpatterns" in transport_types
    ), f"Missing slimpatterns in {transport_types}"
    assert (
        "natspatterns" in transport_types
    ), f"Missing natspatterns in {transport_types}"
    assert "jsonrpc" in transport_types, f"Missing jsonrpc in {transport_types}"
    assert "slimrpc" in transport_types, f"Missing slimrpc in {transport_types}"

    # Verify no containers were actually started
    assert (
        len(session.app_containers) == 0
    ), f"dry_run=True should not create containers, found {len(session.app_containers)}"

    print("=== ✅ test_dry_run passed ===")


# ---------------------------------------------------------------------------
# test_dry_run_with_skip — verify .skip() removes entries from plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dry_run_with_skip():
    """``add_a2a_card().skip("jsonrpc").dry_run()`` omits jsonrpc from the plan."""
    factory = AgntcyFactory(enable_tracing=True)
    agent_card = make_agent_card("dry_run_skip_topic")

    env_patch = {"SLIM_SHARED_SECRET": "test-secret-for-dry-run-only"}
    with patch.dict("os.environ", env_patch):
        session = factory.create_app_session(max_sessions=10)
        plan = await (
            session.add_a2a_card(agent_card, None)
            .with_factory(factory)
            .skip("jsonrpc")
            .dry_run()
        )

    assert isinstance(plan, ServeCardPlan)
    assert (
        len(plan.containers) == 3
    ), f"Expected 3 plan entries (SLIMRPC, SLIM, NATS), got {len(plan.containers)}"

    transport_types = [entry["transport"] for entry in plan.containers]
    assert (
        "jsonrpc" not in transport_types
    ), f"jsonrpc should have been skipped: {transport_types}"
    assert "slimpatterns" in transport_types
    assert "natspatterns" in transport_types
    assert "slimrpc" in transport_types

    # Verify no containers were actually started
    assert len(session.app_containers) == 0

    print("=== ✅ test_dry_run_with_skip passed ===")
