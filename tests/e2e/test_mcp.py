from gateway_sdk.factory import GatewayFactory
from a2a.types import (
    MessageSendParams,
    SendMessageRequest,
)
from typing import Any
import uuid
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_client():
    """
    End-to-end test for the A2A factory client over different transports.
    """

    # Endpoint for local test mcp server
    endpoint = "http://localhost:8123/mcp"
    transport = "StreamableHTTP"

    print(
        f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
    )

    # Create factory and transport
    print("[setup] Initializing client factory and transport...")
    factory = GatewayFactory()
    transport_instance = factory.create_transport(transport=transport, endpoint=endpoint)

    # Create A2A client
    print("[test] Creating MCP client...")
    client = await factory.create_client(
        "MCP",
        agent_url=endpoint,
        transport=transport_instance,
    )
    assert client is not None, "Client was not created"

    print(f"[debug] Client type: {client.type()}")

    # Build message request
    print("[test] Sending test message...")
    try:
        result = await client.session.call_tool(
                    name="get_forecast",
                    arguments={"location": "Colombia"},
                )
        print(f"Tool call result: {result}")

        response = ""
        if hasattr(result, "__aiter__"):
            # gather streamed chunks
            async for chunk in result:
                delta = chunk.choices[0].delta
                response += delta.content or ""
        else:
            content_list = result.content
            if isinstance(content_list, list) and len(content_list) > 0:
                response = content_list[0].text
            else:
                response = "No content returned from tool."

        assert response is not None, "Response was None"

        print(f"[debug] Raw response: {response}")
    except Exception as e:
        print(f"[error] Failed to send message: {e}")
        raise
    finally:
        await client.cleanup()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")
