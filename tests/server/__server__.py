# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agent_executor import (
    HelloWorldAgentExecutor,  # type: ignore[import-untyped]
)
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import asyncio
import argparse
from uvicorn import Config, Server
from agntcy_app_sdk.factory import TransportTypes
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory(enable_tracing=True)


async def main(transport_type: str, endpoint: str, version="1.0.0", block: bool = True):
    """
    This is a simple example of how to create a bridge between an A2A server and a transport.
    It creates a Hello World agent and sets up the transport to communicate with it.
    """
    skill = AgentSkill(
        id="hello_world",
        name="Returns hello world",
        description="just returns hello world",
        tags=["hello world"],
        examples=["hi", "hello world"],
    )

    agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        url="http://localhost:9999/",
        version=version,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],  # Only the basic skill for the public card
        supportsAuthenticatedExtendedCard=False,
    )

    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    if transport_type == "A2A":
        config = Config(app=server.build(), host="0.0.0.0", port=9999, loop="asyncio")
        userver = Server(config)
        await userver.serve()
    else:
        transport = factory.create_transport(transport_type, endpoint=endpoint)
        bridge = factory.create_bridge(server, transport=transport)
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
        "--version",
        type=str,
        default="1.0.0",
        help="Version of the agent (default: 1.0.0)",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_false",
        dest="block",
        help="Run the server in non-blocking mode (default: blocking)",
    )

    args = parser.parse_args()

    asyncio.run(main(args.transport, args.endpoint, args.version, args.block))
