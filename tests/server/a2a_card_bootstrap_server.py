# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A2A server that bootstraps via ``add_a2a_card()`` — the way a real user would.

The agent card is the **single source of truth**.  It declares *all*
available transports in ``supported_interfaces`` (SLIM, NATS, HTTP); the
list order signals which one clients should favour (first = preferred).
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
from urllib.parse import urlparse

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

# ---------------------------------------------------------------------------
# Shared test-service settings (single source of truth)
#
# This module is the one place where the e2e server, the e2e fixtures and the
# e2e clients get the SLIM / NATS endpoints and the SLIM shared secret from.
# The defaults match docker-compose (``services/docker/docker-compose.yaml``);
# set ``SLIM_ENDPOINT`` / ``NATS_ENDPOINT`` / ``SLIM_SHARED_SECRET`` to point
# the tests at other services.  The first two are the same variables the SDK's
# ``add_a2a_card()`` reads, so the SDK and the tests always agree.  Values are
# read when called, and the server subprocess inherits the environment.
# ---------------------------------------------------------------------------

_DEFAULT_SLIM_ENDPOINT = "http://localhost:46357"
_DEFAULT_NATS_ENDPOINT = "nats://localhost:4222"

# Placeholder SLIM MLS secret used when SLIM_SHARED_SECRET is unset.  Same
# value as the SDK's own default (SLIMTransport).  Not a production credential.
DEFAULT_SLIM_SHARED_SECRET = "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"


def slim_endpoint() -> str:
    """SLIM dataplane endpoint as the SDK expects it: ``http://host:port``."""
    return os.environ.get("SLIM_ENDPOINT", _DEFAULT_SLIM_ENDPOINT)


def nats_endpoint() -> str:
    """NATS endpoint as the SDK expects it: ``nats://host:port``."""
    return os.environ.get("NATS_ENDPOINT", _DEFAULT_NATS_ENDPOINT)


def slim_card_endpoint() -> str:
    """SLIM endpoint in agent-card form (``slim://host:port``)."""
    return f"slim://{urlparse(slim_endpoint()).netloc}"


def nats_card_endpoint() -> str:
    """NATS endpoint in agent-card form (``nats://host:port``)."""
    return nats_endpoint()


def get_slim_shared_secret() -> str:
    """Secret the server uses: ``SLIM_SHARED_SECRET`` if set, else the default."""
    return os.environ.get("SLIM_SHARED_SECRET", DEFAULT_SLIM_SHARED_SECRET)


DEFAULT_SKILL = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)

# Map CLI --transport values to the InterfaceTransport (protocol_binding) strings
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
    interfaces = [
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_PATTERNS,
            url=f"{slim_card_endpoint()}/{name}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.NATS_PATTERNS,
            url=f"{nats_card_endpoint()}/{name}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.JSONRPC,
            url=f"http://0.0.0.0:{port}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_RPC,
            url=f"{slim_card_endpoint()}/{name}",
        ),
    ]
    # List order = server preference: move the requested transport to the front.
    preferred = _PREFERRED_TRANSPORT[transport_type]

    # SLIM 2.x delivers a session to ONE subscriber of a name, so a pub/sub
    # (slim) interface and an RPC (slimrpc) interface must not share the same
    # identity.  Declare only the SLIM flavour that the test exercises.
    if preferred == InterfaceTransport.SLIM_RPC:
        unused = {InterfaceTransport.SLIM_PATTERNS}
    else:
        unused = {InterfaceTransport.SLIM_RPC}
    interfaces = [i for i in interfaces if i.protocol_binding not in unused]
    interfaces.sort(key=lambda i: i.protocol_binding != preferred)

    agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        version=version,
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[DEFAULT_SKILL],
        supported_interfaces=interfaces,
    )

    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(name),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
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
    # add_a2a_card() requires SLIM_SHARED_SECRET for SLIM transports.
    os.environ["SLIM_SHARED_SECRET"] = get_slim_shared_secret()

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
        help=(
            "Ignored; kept for CLI compatibility.  Endpoints come from the "
            "SLIM_ENDPOINT / NATS_ENDPOINT environment variables "
            "(default: localhost, see docker-compose)."
        ),
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
