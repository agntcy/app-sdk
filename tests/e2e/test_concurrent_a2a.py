# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from a2a.types import (
    MessageSendParams,
    SendMessageRequest,
)
from typing import Any
import uuid
import asyncio
import pytest
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_server, transport):
    """
    End-to-end test for the A2A factory client over different transports with concurrent requests.
    """
    endpoint = TRANSPORT_CONFIGS[transport]
    print(
        f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
    )

    # Launch the server for each version (concurrently, if applicable)
    print("[setup] Launching test servers...")
    for version in ["1.0.0", "2.0.0", "3.0.0"]:
        run_server(transport, endpoint, version=version)

    # Initialize factory and transport
    print("[setup] Initializing client factory and transport...")

    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(transport, endpoint=endpoint)

    # Create clients concurrently
    print("[setup] Creating clients...")
    agent_versions = ["1.0.0", "2.0.0", "3.0.0"]
    clients = await asyncio.gather(
        *[
            factory.create_client(
                "A2A",
                agent_url=endpoint,
                agent_topic=f"Hello_World_Agent_{v}",
                transport=transport_instance,
            )
            for v in agent_versions
        ]
    )

    assert all(clients), "One or more clients were not created"

    for i, client in enumerate(clients, 1):
        print(f"[info] Client{i} name: {client.agent_card.name}")

    # Prepare request
    send_message_payload: dict[str, Any] = {
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": "how much is 10 USD in INR?"}],
            "messageId": "1234",
        },
    }
    request = SendMessageRequest(
        id=str(uuid.uuid4()), params=MessageSendParams(**send_message_payload)
    )

    # Send messages concurrently
    print("[test] Sending concurrent messages...")
    responses = await asyncio.gather(
        *[client.send_message(request) for client in clients]
    )

    assert all(responses), "One or more responses were None"

    for i, response in enumerate(responses, 1):
        json_response = response.model_dump(mode="json", exclude_none=True)
        print(f"[response {i}] {json_response}")

    # wait for all tasks to complete
    print("[teardown] Waiting for all tasks to complete...")
    await asyncio.sleep(1)

    if transport_instance:
        print("[teardown] Closing transport...")
        await transport_instance.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")
