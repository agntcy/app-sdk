# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
import asyncio


async def test_mcp():
    """
    End-to-end test for the MCP factory client and bridge.
    """

    # Endpoint for local test mcp server
    endpoint = "http://localhost:46357"
    transport = "SLIM"

    print(
        f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
    )

    # Create factory and transport
    print("[setup] Initializing client factory and transport...")
    factory = AgntcyFactory()
    transport_instance = factory.create_transport(
        transport=transport, endpoint=endpoint
    )

    client = factory.create_client(
        "MCP",
        agent_topic="test_topic.mcp",
        transport=transport_instance,
    )

    async with client as mcp_client:
        tools = await mcp_client.list_tools()
        print("[test] Tools available:", tools)


if __name__ == "__main__":
    asyncio.run(test_mcp())
    print("[test] MCP test completed successfully.")
