from gateway_sdk.factory import GatewayFactory
from gateway_sdk.factory import ProtocolTypes
from gateway_sdk.protocols.a2a.gateway import A2AClient
from typing import Any
import pytest

@pytest.mark.asyncio
async def test_default_client():
    """
    Test the A2A factory client creation.
    """
    factory = GatewayFactory()
    
    client = await factory.create_client("A2A", agent_url="http://localhost:9999")
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
async def test_client_with_nats_transport():
    """
    Test the A2A factory client creation with transport.
    """
    factory = GatewayFactory(enable_tracing=True)

    # Create a Nats transport
    transport = factory.create_transport("NATS", "localhost:4222")
    # or: transport = await nats.connect(self.endpoint)
    # ie: do we support nats.nc object and wrap in the create_client?

    # Create a client with the transport
    client = await factory.create_client(ProtocolTypes.A2A.value, agent_url="http://localhost:9999", transport=transport)
    
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

@pytest.mark.asyncio
async def test_client_with_nats_from_topic():
    """
    Test the A2A factory client creation.
    """
    factory = GatewayFactory(enable_tracing=True)

    transport = factory.create_transport("NATS", "localhost:4222")

    #from gateway_sdk.protocols.a2a.gateway import A2AProtocol
    #topic = A2AProtocol.create_agent_topic(card)
    
    client = await factory.create_client("A2A", agent_topic="Hello_World_Agent_1.0.0", transport=transport)
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

    print("\n=== Success ===")

    await transport.close()

@pytest.mark.asyncio
async def test_client_with_agp_transport():
    factory = GatewayFactory()

    # Create a AGP transport
    transport = factory.create_transport("AGP", "http://localhost:46357")

    client = await factory.create_client("A2A", agent_topic="Hello_World_Agent_1.0.0", transport=transport)

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