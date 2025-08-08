# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from mcp.server.fastmcp import FastMCP
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.factory import TransportTypes
import asyncio
import argparse

factory = AgntcyFactory(enable_tracing=False)


async def main(transport_type: str, endpoint: str, block: bool = True):
    # Create the MCP server
    mcp = FastMCP()

    @mcp.tool()
    async def get_forecast(location: str) -> str:
        return "Temperature: 30°C\n" "Humidity: 50%\n" "Condition: Sunny\n"

    transport = factory.create_transport(transport_type, endpoint=endpoint)
    bridge = factory.create_bridge(mcp, transport=transport, topic="test_topic.mcp")
    await bridge.start(blocking=block)


if __name__ == "__main__":
    # get transport type from command line argument
    parser = argparse.ArgumentParser(
        description="Run the A2A server with a specified transport type."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=[t.value for t in TransportTypes],
        default=TransportTypes.NATS.value,
        help="Transport type to use (default: NATS)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="localhost:4222",
        help="Endpoint for the transport (default: localhost:4222)",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_false",
        dest="block",
        help="Run the server in non-blocking mode (default: blocking)",
    )

    args = parser.parse_args()

    asyncio.run(main(args.transport, args.endpoint, args.block))
