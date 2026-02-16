# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from uuid import uuid4

import pytest
from a2a.client import ClientFactory, minimal_agent_card
from a2a.types import Message, Part, Role, TextPart

from slima2a import setup_slim_client
from slima2a.client_transport import (
    ClientConfig as SRPCClientConfig,
    SRPCTransport,
    slimrpc_channel_factory,
)

from agntcy_app_sdk.semantic.a2a import A2AClientConfig, A2AClientFactory
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
    assert (
        "slimrpc" in config.supported_transports
    ), f"Expected 'slimrpc' in supported_transports, got: {config.supported_transports}"

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
