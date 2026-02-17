# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

try:
    from tests.server.agent_executor import (
        HelloWorldAgentExecutor,  # type: ignore[import-untyped]
    )
except ImportError:
    from agent_executor import (
        HelloWorldAgentExecutor,  # type: ignore[import-untyped]
    )

import argparse
import asyncio

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory(enable_tracing=True)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

DEFAULT_SKILL = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)


def _build_a2a_server(
    name: str = "Default_Hello_World_Agent",
    version: str = "1.0.0",
) -> A2AStarletteApplication:
    """Build an A2A server with a HelloWorld agent."""
    agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        url="http://localhost:9999/",
        version=version,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[DEFAULT_SKILL],
        supportsAuthenticatedExtendedCard=False,
    )
    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(name),
        task_store=InMemoryTaskStore(),
    )
    return A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler)


# Module-level instance used by unit tests
default_a2a_server = _build_a2a_server()

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def main(
    transport_type: str,
    name: str,
    topic: str,
    endpoint: str,
    version: str = "1.0.0",
    block: bool = True,
):
    """Create a bridge between an A2A server and a transport."""
    server = _build_a2a_server(name=name, version=version)

    if transport_type == "JSONRPC":
        # No transport — AppSession will use A2AJsonRpcServerHandler
        # Parse host/port from the endpoint URL
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        host = parsed.hostname or "0.0.0.0"
        port = parsed.port or 9999

        app_session = factory.create_app_session(max_sessions=1)
        app_session.add(server).with_host(host).with_port(port).with_session_id(
            "default_session"
        ).build()
        await app_session.start_all_sessions(keep_alive=block)
    else:
        print(f"Creating transport for {transport_type} at {endpoint} with name {name}")
        transport = factory.create_transport(
            transport_type, endpoint=endpoint, name=name
        )

        app_session = factory.create_app_session(max_sessions=1)
        app_session.add(server).with_transport(transport).with_topic(
            topic
        ).with_session_id("default_session").build()
        await app_session.start_all_sessions(keep_alive=block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the A2A server with a specified transport type."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=[*AgntcyFactory().registered_transports(), "JSONRPC"],
        default="NATS",
        help="Transport type to use (default: NATS)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/Hello_World_Agent_1.0.0",
        help="Routable name for the transport (default: default/default/Hello_World_Agent_1.0.0)",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="Topic for A2A communication (default: auto-derived from agent card)",
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
    asyncio.run(
        main(
            args.transport,
            args.name,
            args.topic,
            args.endpoint,
            args.version,
            args.block,
        )
    )
