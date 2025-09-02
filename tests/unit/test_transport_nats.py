from agntcy_app_sdk.transports.nats.transport import NatsTransport


def test_extract_message_payload_ids_realistic():
    t = NatsTransport(endpoint="nats://localhost:4222")

    # Case 1: Message with no id or messageId (path/method only)
    payload1 = {"path": ".well-known/agent.json", "method": "GET"}
    id_, message_id = t._extract_message_payload_ids(payload1)
    assert id_ is None
    assert message_id is None

    # Case 2: Message with both id and nested messageId
    payload2 = {
        "id": "c8f0f828-978a-4503-a613-17822b05a87c",
        "jsonrpc": "2.0",
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "messageId": "89186480-3f05-4098-ab06-2610a66afa93",
                "parts": [
                    {
                        "kind": "text",
                        "text": "Please provide the total coffee yield inventory across all farms.",
                    }
                ],
                "role": "user",
            }
        },
    }
    id_, message_id = t._extract_message_payload_ids(payload2)
    assert id_ == "c8f0f828-978a-4503-a613-17822b05a87c"
    assert message_id == "89186480-3f05-4098-ab06-2610a66afa93"

    # Case 3: Same as above but as JSON string
    import json

    payload2_str = json.dumps(payload2)
    id_, message_id = t._extract_message_payload_ids(payload2_str)
    assert id_ == "c8f0f828-978a-4503-a613-17822b05a87c"
    assert message_id == "89186480-3f05-4098-ab06-2610a66afa93"
