# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Agent server using card-driven bootstrap — adapted from
A2A_USAGE_GUIDE.md Example 2.

The agent card declares ALL available transports in ``additional_interfaces``.
``add_a2a_card()`` reads those interfaces and starts everything with a single
call — no manual transport creation or builder chains required.

Usage:
    uv run python tests/guide_examples/weather_agent_card.py \
        --transport SLIM --slim-endpoint http://localhost:46357
"""

import argparse
import asyncio
import os

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.utils import new_agent_text_message

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SLIM_ENDPOINT = "slim://localhost:46357"
NATS_ENDPOINT = "nats://localhost:4222"

# Map CLI --transport to InterfaceTransport preferredTransport values
_PREFERRED_TRANSPORT: dict[str, str] = {
    "SLIM": InterfaceTransport.SLIM_PATTERNS,
    "NATS": InterfaceTransport.NATS_PATTERNS,
}

# ---------------------------------------------------------------------------
# Agent card & skill
# ---------------------------------------------------------------------------

skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)


def build_agent_card(
    transport_type: str,
    slim_endpoint: str = SLIM_ENDPOINT,
    nats_endpoint: str = NATS_ENDPOINT,
) -> AgentCard:
    """Build an AgentCard with transport interfaces based on the preferred transport."""
    name = "default/default/Weather_Agent_1.0.0"

    return AgentCard(
        name="Weather Agent",
        description="An agent that provides weather reports",
        url="",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
        supportsAuthenticatedExtendedCard=False,
        preferredTransport=_PREFERRED_TRANSPORT[transport_type],
        additional_interfaces=[
            AgentInterface(
                transport=InterfaceTransport.SLIM_PATTERNS,
                url=f"{slim_endpoint}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.NATS_PATTERNS,
                url=f"{nats_endpoint}/{name}",
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Agent logic
# ---------------------------------------------------------------------------


class WeatherAgent:
    """A simple agent that returns a weather report."""

    async def invoke(self) -> str:
        return "The weather is sunny with a high of 75F."


class WeatherAgentExecutor(AgentExecutor):
    def __init__(self):
        self.agent = WeatherAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        result = await self.agent.invoke()
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main(transport_type: str, slim_endpoint: str, nats_endpoint: str):
    agent_card = build_agent_card(transport_type, slim_endpoint, nats_endpoint)

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    # One call does it all — add_a2a_card() reads the card's interfaces
    # and creates transports automatically.
    factory = AgntcyFactory()
    session = factory.create_app_session(max_sessions=10)
    await (
        session.add_a2a_card(agent_card, request_handler)
        .with_factory(factory)
        .start(keep_alive=True)
    )


if __name__ == "__main__":
    # Set SLIM_SHARED_SECRET if not already set — add_a2a_card() requires it
    # for SLIM transports.
    if "SLIM_SHARED_SECRET" not in os.environ:
        os.environ["SLIM_SHARED_SECRET"] = (
            "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"
        )

    parser = argparse.ArgumentParser(
        description="Weather Agent (Card-Driven Bootstrap)"
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["SLIM", "NATS"],
        default="SLIM",
        help="Preferred transport type (default: SLIM)",
    )
    parser.add_argument(
        "--slim-endpoint",
        type=str,
        default=SLIM_ENDPOINT,
        help=f"SLIM endpoint URL (default: {SLIM_ENDPOINT})",
    )
    parser.add_argument(
        "--nats-endpoint",
        type=str,
        default=NATS_ENDPOINT,
        help=f"NATS endpoint URL (default: {NATS_ENDPOINT})",
    )
    args = parser.parse_args()
    asyncio.run(main(args.transport, args.slim_endpoint, args.nats_endpoint))
