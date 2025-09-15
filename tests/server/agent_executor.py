# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
import asyncio
import random
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class HelloWorldAgent:
    """Hello World Agent."""

    def __init__(self, name: str):
        self.name = name

    async def invoke(self, context: RequestContext) -> str:
        prompt = context.get_user_input()
        if "groupchat" in prompt.lower():
            # add a random sleep so we dont get a flood of messages
            chatter_sleep = random.uniform(0.5, 3.0)
            await asyncio.sleep(chatter_sleep)

            # immediately return the DELIVERED message to trigger end_message
            return (
                "DELIVERED by "
                + self.name
                + " in groupchat after sleeping "
                + str(chatter_sleep)
                + " seconds"
            )

        return "Hello from " + self.name


class HelloWorldAgentExecutor(AgentExecutor):
    """Test AgentProxy Implementation."""

    def __init__(self, name: str):
        self.agent = HelloWorldAgent(name)

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        result = await self.agent.invoke(context)
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
