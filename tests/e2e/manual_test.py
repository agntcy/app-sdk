from gateway_sdk.factory import GatewayFactory
from a2a.types import (
    MessageSendParams,
    SendMessageRequest,
)
from typing import Any
import uuid
from tests.e2e.conftest import TRANSPORT_CONFIGS


async def test_client():
    """
    End-to-end test for the A2A factory client over different transports.
    """

    transport = "NATS"
    # Get the endpoint inside the test using the transport name as a key
    endpoint = TRANSPORT_CONFIGS[transport]

    print(
        f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
    )

    # Create factory and transport
    print("[setup] Initializing client factory and transport...")
    factory = GatewayFactory()
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

    responses = await client.broadcast_message(
        request,
        limit=2,
    )

    print(f"[debug] Broadcast responses: {responses}")

    if transport_instance:
        print("[teardown] Closing transport...")
        await transport_instance.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")


if __name__ == "__main__":
    import asyncio

    # Run the test client
    asyncio.run(test_client())
