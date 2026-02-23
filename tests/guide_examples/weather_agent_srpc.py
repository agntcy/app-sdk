# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Agent server using SlimRPC — adapted from A2A_USAGE_GUIDE.md Example 1.

Usage:
    uv run python tests/guide_examples/weather_agent_srpc.py \
        --endpoint http://localhost:46357 \
        --name default/default/weather-agent
"""

import argparse
import asyncio

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from a2a.utils import new_agent_text_message

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.srpc import (
    A2ASlimRpcServerConfig,
    SlimRpcConnectionConfig,
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


async def main(endpoint: str, name: str):
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    srpc_config = A2ASlimRpcServerConfig(
        agent_card=agent_card,
        request_handler=request_handler,
        connection=SlimRpcConnectionConfig(
            identity=name,
            shared_secret="secretsecretsecretsecretsecretsecret",
            endpoint=endpoint,
        ),
    )

    session = factory.create_app_session(max_sessions=1)
    session.add(srpc_config).with_session_id("weather").build()

    await session.start_all_sessions(keep_alive=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weather Agent (SlimRPC)")
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:46357",
        help="SLIM endpoint",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="default/default/weather-agent",
        help="SLIM identity",
    )
    args = parser.parse_args()
    asyncio.run(main(args.endpoint, args.name))
