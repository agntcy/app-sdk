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

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.srpc import (
    A2ASlimRpcServerConfig,
    SlimRpcConnectionConfig,
)

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


def _build_a2a_slimrpc_config(
    name: str = "default/default/Hello_World_Agent_1.0.0",
    endpoint: str = "http://localhost:46357",
    version: str = "1.0.0",
) -> A2ASlimRpcServerConfig:
    """Build an A2ASlimRpcServerConfig with a HelloWorld agent."""
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
    return A2ASlimRpcServerConfig(
        agent_card=agent_card,
        request_handler=request_handler,
        connection=SlimRpcConnectionConfig(
            identity=name,
            shared_secret="secretsecretsecretsecretsecretsecret",
            endpoint=endpoint,
        ),
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


async def main(
    name: str,
    endpoint: str,
    version: str = "1.0.0",
    block: bool = True,
):
    """Create and start an A2A server over SlimRPC."""
    config = _build_a2a_slimrpc_config(
        name=name,
        endpoint=endpoint,
        version=version,
    )

    factory = AgntcyFactory(enable_tracing=True)
    app_session = factory.create_app_session(max_sessions=1)
    app_session.add(config).with_session_id("default_session").build()
    await app_session.start_all_sessions(keep_alive=block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the A2A server over SlimRPC (no BaseTransport layer)."
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/Hello_World_Agent_1.0.0",
        help="Routable identity for SLIM (default: default/default/Hello_World_Agent_1.0.0)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:46357",
        help="SLIM endpoint (default: http://localhost:46357)",
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
            args.name,
            args.endpoint,
            args.version,
            args.block,
        )
    )
