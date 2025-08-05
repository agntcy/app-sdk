# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from mcp.server.fastmcp import FastMCP
from mcp.server.lowlevel import Server
from mcp import types
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

    app: Server = Server("mcp-time")

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        """
        List available time tools.

        Returns:
            list[types.Tool]: List of available time-related tools
        """
        return [
            types.Tool(
                name="get_current_time",
                description="Get current time in a specific timezones",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use as local timezone if no timezone provided by the user.",
                        }
                    },
                    "required": ["timezone"],
                },
            ),
            types.Tool(
                name="convert_time",
                description="Convert time between timezones",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "source_timezone": {
                            "type": "string",
                            "description": "Source IANA timezone name (e.g., 'America/New_York', 'Europe/London'). Use as local timezone if no source timezone provided by the user.",
                        },
                        "time": {
                            "type": "string",
                            "description": "Time to convert in 24-hour format (HH:MM)",
                        },
                        "target_timezone": {
                            "type": "string",
                            "description": "Target IANA timezone name (e.g., 'Asia/Tokyo', 'America/San_Francisco'). Use as local timezone if no target timezone provided by the user.",
                        },
                    },
                    "required": ["source_timezone", "time", "target_timezone"],
                },
            ),
        ]

    # Create an MCP server instance
    mcp = FastMCP()

    @mcp.tool()
    async def get_forecast(location: str) -> str:
        return "Temperature: 80°C\n" "Wind speed: 10 m/s\n" "Wind direction: 180°"

    bridge = factory.create_bridge(
        server=mcp,
        transport=transport_instance,
        topic="test_topic.mcp",
    )
    await bridge.start(blocking=True)


if __name__ == "__main__":
    asyncio.run(test_mcp())
    print("[test] MCP test completed successfully.")
