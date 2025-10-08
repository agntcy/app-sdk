import asyncio
import argparse
from agntcy_app_sdk.discovery.mcp.agent_directory import MCPAgentDirectory
from agntcy_app_sdk.factory import TransportTypes
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory(enable_tracing=False)


async def main(
    transport_type: str,
    name: str,
    topic: str,
    endpoint: str,
    server_http: bool = True,
    host: str = "0.0.0.0",
    port: int = 9868,
    version="1.0.0",
    block: bool = True,
):
    transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)

    directory = MCPAgentDirectory()
    await directory.serve(
        transport=transport,
        topic=topic,
        blocking=block,
        serve_http=server_http,
        host=host,
        port=port,
    )


if __name__ == "__main__":
    # get transport type from command line argument
    parser = argparse.ArgumentParser(
        description="Run the MCPAgentDirectory server with given transport"
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=[t.value for t in TransportTypes],
        default=TransportTypes.NATS.value,
        help="Transport type to use (default: NATS)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/mcp_agent_directory",
        help="Routable name for the transport in the form 'org/namespace/local_name' (default: default/default/mcp_agent_directory)",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default="mcp_agent_directory",
        help="Topic for agent discovery",
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

    asyncio.run(
        main(
            args.transport,
            args.name,
            args.topic,
            args.endpoint,
            args.block,
        )
    )
