# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from mcp.server.fastmcp import FastMCP
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
    factory = AgntcyFactory(log_level="DEBUG")
    transport_instance = factory.create_transport(
        transport=transport, endpoint=endpoint
    )

    # Create an MCP server instance
    mcp = FastMCP()
    bridge = factory.create_bridge(
        server=mcp,
        transport=transport_instance,
        topic="test_topic.mcp",
    )
    await bridge.start(blocking=True)


if __name__ == "__main__":
    asyncio.run(test_mcp())
    print("[test] MCP test completed successfully.")
