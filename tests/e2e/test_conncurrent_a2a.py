# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from a2a.types import (
    MessageSendParams,
    SendMessageRequest,
)
from typing import Any
import uuid
import pytest
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_server, transport):
    """
    End-to-end test for the A2A factory client over different transports.
    """
    # Get the endpoint inside the test using the transport name as a key
    endpoint = TRANSPORT_CONFIGS[transport]

    print(
        f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
    )

    # Start the mock/test server
    print("[setup] Launching test server...")
    run_server(transport, endpoint)

    # Create factory and transport
    print("[setup] Initializing client factory and transport...")
    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(transport, endpoint=endpoint)

    # Create A2A client
    print("[test] Creating A2A client...")
    client = await factory.create_client(
        "A2A",
        agent_url=endpoint,
        agent_topic="Hello_World_Agent_1.0.0",  # Used if transport is provided
        transport=transport_instance,
    )
    assert client is not None, "Client was not created"

    print("\n=== Agent Information ===")
    print(f"Name: {client.agent_card.name}")

    # Build message request
    print("[test] Sending test message...")
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

    # Send and validate response
    response = await client.send_message(request)
    assert response is not None, "Response was None"

    response = response.model_dump(mode="json", exclude_none=True)

    print(f"[debug] Raw response: {response}")

    assert response["jsonrpc"] == "2.0"
    assert response["result"]["kind"] == "message"
    assert response["result"]["role"] == "agent"

    parts = response["result"]["parts"]
    assert isinstance(parts, list)
    assert parts[0]["kind"] == "text"
    assert parts[0]["text"] == "Hello World"

    print(f"[result] Agent responded with: {parts[0]['text']}")

    if transport_instance:
        print("[teardown] Closing transport...")
        await transport_instance.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")
