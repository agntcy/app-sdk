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

    '''send_message_payload: dict[str, Any] = {
        'message': {
            'role': 'user',
            'parts': [
                {'type': 'text', 'text': 'how much is 10 USD in INR?'}
            ],
            'messageId': "1234",
        },
    }

    response = await client.send_message(payload=send_message_payload)
    print(response.model_dump(mode='json', exclude_none=True))'''