import nats
import json
import logging
import pytest

pytest_plugins = "pytest_asyncio"

logging.basicConfig(level=logging.INFO)

@pytest.mark.asyncio
async def test_pubsub_predict():
    # test the predict endpoint
    nc = await nats.connect("localhost:4222")

    topic = "Hello_World_Agent_1_0_0"
    message = {
        "path": ".well-known/agent.json",
        "method": "GET",
    }

    payload = json.dumps(message).encode()
    headers = {"trace-id": "1234"}

    response = await nc.request(
        topic, payload=payload, headers=headers, timeout=2
    )

    data = json.loads(response.data)

    print(f"Received response: {data}")

    await nc.drain()