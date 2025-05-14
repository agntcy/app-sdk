from gateway_sdk.factory import GatewayFactory
from typing import Any
import pytest

@pytest.mark.asyncio
async def test_a2a_factory_client():
    """
    Test the A2A factory client creation.
    """
    factory = GatewayFactory()
    
    client = await factory.create_client("A2A", "http://localhost:9999")
    assert client is not None

    print("\n=== Agent Information ===")
    print(f"Name: {client.agent_card}")

    assert client is not None

    send_message_payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [
                {'type': 'text', 'text': 'how much is 10 USD in INR?'}
            ],
            'messageId': "1234",
        },
    }

    response = await client.send_message(payload=send_message_payload)
    print(response.model_dump(mode='json', exclude_none=True))

@pytest.mark.asyncio
async def test_a2a_factory_client_with_transport():
    """
    Test the A2A factory client creation with transport.
    """
    factory = GatewayFactory() # TODO: consider separate factories

    # Create a Nats transport
    transport = factory.create_transport("NATS", "localhost:4222", options={})
    # or: transport = await nats.connect(self.endpoint)
    # ie: do we support nats.nc object and wrap in the create_client?

    # Create a client with the transport
    client = await factory.create_client("A2A", agent_endpoint="http://localhost:9999", transport=transport)
    
    assert client is not None

    send_message_payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [
                {'type': 'text', 'text': 'how much is 10 USD in INR?'}
            ],
            'messageId': "1234",
        },
    }

    response = await client.send_message(payload=send_message_payload)
    assert response is not None

    print("remote agent responded with: \n", response.model_dump(mode='json', exclude_none=True))

    print("\n=== Success ===")

    await transport.close()

    print("\n=== Transport Closed ===")