# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A2A server that bootstraps via ``add_a2a_card()`` — the way a real user would.

The agent card is the **single source of truth**.  It declares *all*
available transports in ``additional_interfaces`` (SLIM, NATS, HTTP) and
uses ``preferredTransport`` to signal which one clients should favour.
``add_a2a_card()`` reads those interfaces and wires everything up — no
manual builder chain required.

Compare with ``a2a_starlette_server.py`` which uses the manual
``session.add(server).with_transport(…).with_topic(…).build()`` pattern.
"""

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
import os

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

# Well-known test-service endpoints (must match docker-compose / conftest)
SLIM_ENDPOINT = "slim://localhost:46357"
NATS_ENDPOINT = "nats://localhost:4222"

DEFAULT_SKILL = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)

# Map CLI --transport values to InterfaceTransport preferredTransport strings
_PREFERRED_TRANSPORT: dict[str, str] = {
    "SLIM": InterfaceTransport.SLIM_PATTERNS,
    "NATS": InterfaceTransport.NATS_PATTERNS,
    "JSONRPC": InterfaceTransport.JSONRPC,
    "SLIMRPC": InterfaceTransport.SLIM_RPC,
}


# ---------------------------------------------------------------------------
# CLI entry point — card is built declaratively, add_a2a_card() does the rest
# ---------------------------------------------------------------------------


async def main(
    transport_type: str,
    name: str,
    version: str = "1.0.0",
    port: int = 9999,
    block: bool = True,
):
    """Start an A2A server using ``session.add_a2a_card()``."""

    # -- Build the card as a real user would: declare ALL transports --------
    # The *name* is the agent's routable identity, stamped into the SLIM/NATS
    # interface URLs.  add_a2a_card() reads those URLs and subscribes accordingly.
    agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        url=f"http://localhost:{port}/",
        version=version,
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[DEFAULT_SKILL],
        supportsAuthenticatedExtendedCard=False,
        preferredTransport=_PREFERRED_TRANSPORT[transport_type],
        additional_interfaces=[
            AgentInterface(
                transport=InterfaceTransport.SLIM_PATTERNS,
                url=f"{SLIM_ENDPOINT}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.NATS_PATTERNS,
                url=f"{NATS_ENDPOINT}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.JSONRPC,
                url=f"http://0.0.0.0:{port}",
            ),
            AgentInterface(
                transport=InterfaceTransport.SLIM_RPC,
                url=f"{SLIM_ENDPOINT}/{name}",
            ),
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(name),
        task_store=InMemoryTaskStore(),
    )

    # -- One call does it all -----------------------------------------------
    factory = AgntcyFactory(enable_tracing=True)
    session = factory.create_app_session(max_sessions=10)
    await (
        session.add_a2a_card(agent_card, request_handler)
        .with_factory(factory)
        .start(keep_alive=block)
    )


if __name__ == "__main__":
    # Set SLIM_SHARED_SECRET if not already set — add_a2a_card() requires it
    # for SLIM transports.  Use the same default as SLIMTransport.__init__.
    if "SLIM_SHARED_SECRET" not in os.environ:
        os.environ[
            "SLIM_SHARED_SECRET"
        ] = "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"

    parser = argparse.ArgumentParser(
        description="Run the A2A server using add_a2a_card() bootstrap."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=list(_PREFERRED_TRANSPORT.keys()),
        default="NATS",
        help="Preferred transport type (default: NATS)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/Hello_World_Agent_1.0.0",
        help="Routable name for the transport",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="localhost:4222",
        help="Endpoint (kept for CLI compatibility, card endpoints are authoritative)",
    )
    parser.add_argument(
        "--version",
        type=str,
        default="1.0.0",
        help="Version of the agent (default: 1.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9999,
        help="HTTP port for JSONRPC interface (default: 9999)",
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
            args.version,
            args.port,
            args.block,
        )
    )
