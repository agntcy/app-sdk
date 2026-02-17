# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Agent server using Experimental Patterns (SLIM) — adapted from
A2A_USAGE_GUIDE.md Example 2.

Usage:
    uv run python tests/guide_examples/weather_agent.py \
        --transport SLIM --endpoint http://localhost:46357
"""

import argparse
import asyncio

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AStarletteApplication
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    A2AExperimentalServer,
)

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

agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    url="",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supportsAuthenticatedExtendedCard=False,
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


async def main(transport_type: str, endpoint: str):
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    topic = A2AExperimentalServer.create_agent_topic(agent_card)
    name = f"default/default/{topic}"
    transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)

    session = factory.create_app_session(max_sessions=1)
    session.add(server).with_transport(transport).with_session_id("weather").build()

    await session.start_all_sessions(keep_alive=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Weather Agent (Experimental Patterns)"
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["SLIM", "NATS"],
        default="SLIM",
        help="Transport type (default: SLIM)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:46357",
        help="Transport endpoint",
    )
    args = parser.parse_args()
    asyncio.run(main(args.transport, args.endpoint))
