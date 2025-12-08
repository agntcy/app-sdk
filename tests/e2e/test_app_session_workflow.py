# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
import pytest
import asyncio
from ioa_observe.sdk.tracing import session_start
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_a2a_server, transport):
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
    run_a2a_server(transport, endpoint, publish_record=True)

    await asyncio.sleep(1)  # Give the server a moment to start

    # Create factory and transport
    print("[setup] Initializing client factory and transport...")
    factory = AgntcyFactory(enable_tracing=True)
    transport_instance = factory.create_transport(
        transport, endpoint=endpoint, name="default/default/default"
    )

    session_start()

    # Create A2A client
    print("[test] Creating A2A client...")
    client = await factory.create_client(
        "A2A",
        agent_url=endpoint,
        agent_topic="Hello_World_Agent_1.0.0",
        transport=transport_instance,
    )
    assert client is not None, "Client was not created"

    print("\n=== Agent Information ===")
    print(f"Name: {client.agent_card.name}")

    if transport_instance:
        print("[teardown] Closing transport...")
        await transport_instance.close()
