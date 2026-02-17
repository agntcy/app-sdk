# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import argparse
import asyncio

from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory(enable_tracing=False)


async def main(transport_type: str, endpoint: str, name: str, block: bool = True):
    """Start an MCP server bridged over the given transport."""
    mcp = FastMCP()

    @mcp.tool()
    async def get_forecast(location: str) -> str:
        return "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n"

    transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)

    app_session = factory.create_app_session(max_sessions=1)
    app_session.add(mcp._mcp_server).with_transport(transport).with_topic(
        "mcp"
    ).with_session_id("default_session").build()
    await app_session.start_all_sessions(keep_alive=block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the MCP server with a specified transport type."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=AgntcyFactory().registered_transports(),
        default="NATS",
        help="Transport type to use (default: NATS)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="localhost:4222",
        help="Endpoint for the transport (default: localhost:4222)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/mcp",
        help="Routable name for the transport (default: default/default/mcp)",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_false",
        dest="block",
        help="Run the server in non-blocking mode (default: blocking)",
    )

    args = parser.parse_args()
    asyncio.run(main(args.transport, args.endpoint, args.name, args.block))
