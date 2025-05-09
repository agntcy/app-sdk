from gateway_sdk.factory import GatewayFactory

def test_a2a_factory_client():
    """
    Test the A2A factory client creation.
    """
    factory = GatewayFactory()
    client = factory.create_client("A2A", "http://localhost:5005")
    assert client is not None

    print("\n=== Agent Information ===")
    print(f"Name: {client.agent_card.name}")
    print(f"Description: {client.agent_card.description}")
    print(f"Version: {client.agent_card.version}")

    if client.agent_card.skills:
        print("\nAvailable Skills:")
        for skill in client.agent_card.skills:
            print(f"- {skill.name}: {skill.description}")
            if skill.examples:
                print(f"  Examples: {', '.join(skill.examples)}")

def test_a2a_factory_client_agp():
    """
    Test the A2A factory client creation with AGP transport.
    """
    factory = GatewayFactory()
    client = factory.create_client("A2A", "http://localhost:8080", transport="AGP")
    assert client is not None

def test_a2a_factory_client_nats():
    """
    Test the A2A factory client creation with NATS transport.
    """
    factory = GatewayFactory()
    client = factory.create_client("A2A", "http://localhost:8080", transport="NATS")
    assert client is not None