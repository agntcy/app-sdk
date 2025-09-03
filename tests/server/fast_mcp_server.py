# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import argparse
import asyncio
from agntcy_app_sdk.factory import AgntcyFactory, TransportTypes
from mcp.server.fastmcp import FastMCP

# Initialize the factory with tracing disabled
factory = AgntcyFactory(enable_tracing=False)


async def main(transport_type: str, endpoint: str, name: str, block: bool = True):
    """
    Main function to start the MCP server with the specified transport type and endpoint.

    **Parameters:**
    - `transport_type` (str): The transport type to use (e.g., NATS, HTTP).
    - `endpoint` (str): The endpoint for the transport (e.g., localhost:4222).
    - `block` (bool): Whether to run the server in blocking mode (default: True).

    **Raises:**
    - `Exception`: If an error occurs during server setup or execution.
    """
    try:
        # Create the MCP server instance
        mcp = FastMCP()

        @mcp.tool()
        async def get_forecast(location: str) -> str:
            """
            Tool to fetch the weather forecast for a given location.

            **Parameters:**
            - `location` (str): The location for which the forecast is requested.

            **Returns:**
            - `str`: A string containing the weather forecast.
            """
            return "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n"

        # Create the transport instance
        transport = factory.create_transport(
            transport_type, endpoint=endpoint, name=name
        )
        print(
            f"[setup] Transport created: {transport_type} | Endpoint: {endpoint} | Name: {name}"
        )

        # Create the bridge between MCP and transport
        bridge = factory.create_bridge(mcp, transport=transport, topic="fastmcp")
        print("[setup] Bridge created with topic: fastmcp")

        # Start the bridge
        print("[start] Starting the bridge...")
        await bridge.start(blocking=block)
        print("[start] Bridge started successfully.")

    except Exception as e:
        print(f"[error] Failed to start the MCP server: {e}")
        raise


if __name__ == "__main__":
    # Parse command-line arguments
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
        "--name",
        type=str,
        default="test_server",
        help="Name of the server instance (default: test_server)",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_false",
        dest="block",
        help="Run the server in non-blocking mode (default: blocking)",
    )

    args = parser.parse_args()

    # Run the main function with parsed arguments
    asyncio.run(main(args.transport, args.endpoint, args.name, args.block))
