# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import uuid
from typing import Any

import pytest
from a2a.types import (
    Message,
    MessageSendParams,
    Role,
    SendMessageRequest,
    TextPart,
)
from ioa_observe.sdk.tracing import session_start

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_message(text: str = "how much is 10 USD in INR?") -> Message:
    """Build a simple A2A Message for ``client.send_message()``."""
    return Message(
        role="user",
        parts=[{"type": "text", "text": text}],
        messageId=str(uuid.uuid4()),
    )


def _make_send_request(text: str = "how much is 10 USD in INR?") -> SendMessageRequest:
    """Build a simple A2A SendMessageRequest (for broadcast/groupchat)."""
    payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": text}],
            "messageId": str(uuid.uuid4()),
        },
    }
    return SendMessageRequest(id=str(uuid.uuid4()), params=MessageSendParams(**payload))


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

        a2a_factory = A2AClientFactory(ClientConfig(streaming=False))
        client = await a2a_factory.create_client(url=endpoint)
    else:
        transport_instance = factory.create_transport(
            transport, endpoint=endpoint, name="default/default/default"
        )

        session_start()

        client = await factory.create_client(
            "A2A",
            agent_url=endpoint,
            agent_topic="Hello_World_Agent_1.0.0",
            transport=transport_instance,
        )

    assert client is not None, "Client was not created"
    print(f"Agent: {(await client.get_card()).name}")

    request = _make_message()
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
        run_a2a_server(transport, endpoint, name=name, topic="broadcast")

    await asyncio.sleep(4)

    handshake_topic = (
        "default/default/agent1" if transport_instance.type() == "SLIM" else "broadcast"
    )

    client = await factory.create_client(
        "A2A", agent_topic=handshake_topic, transport=transport_instance
    )
    assert client is not None, "Client was not created"

    request = _make_send_request()
    responses = await client.broadcast_message(
        request,
        broadcast_topic="broadcast",
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
        run_a2a_server(transport, endpoint, name=name, topic="broadcast")

    await asyncio.sleep(3)

    handshake_topic = (
        "default/default/agent1" if transport_instance.type() == "SLIM" else "broadcast"
    )

    client = await factory.create_client(
        "A2A", agent_topic=handshake_topic, transport=transport_instance
    )
    assert client is not None, "Client was not created"

    request = _make_send_request()
    responses = []
    async for resp in client.broadcast_message_streaming(
        request,
        message_limit=3,
        broadcast_topic="broadcast",
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

    handshake_topic = (
        "default/default/foo" if transport_instance.type() == "SLIM" else "broadcast"
    )

    client = await factory.create_client(
        "A2A", agent_topic=handshake_topic, transport=transport_instance
    )
    assert client is not None, "Client was not created"

    request = _make_send_request("This is a groupchat message")
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
        run_a2a_server(transport, endpoint, name=name, topic="zoo")

    await asyncio.sleep(3)

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    handshake_topic = (
        "default/default/foo" if transport_instance.type() == "SLIM" else "broadcast"
    )

    client = await factory.create_client(
        "A2A", agent_topic=handshake_topic, transport=transport_instance
    )
    assert client is not None, "Client was not created"

    request = _make_send_request("This is a groupchat message")
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

    assert (
        len(messages) > 0
    ), "No streaming group chat messages received (possible timeout)"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_groupchat_streaming passed for {transport} ===\n")
