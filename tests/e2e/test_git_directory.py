# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
import tempfile
import shutil
from pathlib import Path
from agntcy_app_sdk.protocols.a2a.protocol import A2AProtocol
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
import pytest
import asyncio
import os
from tests.e2e.conftest import TRANSPORT_CONFIGS

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

factory = AgntcyFactory(enable_tracing=False)


class HelloWorldAgent:
    def __init__(self, name: str):
        self.name = name

    async def invoke(self, context: RequestContext) -> str:
        return "Hello from " + self.name


class HelloWorldAgentExecutor(AgentExecutor):
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
    version="0.1.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supportsAuthenticatedExtendedCard=False,
)

request_handler = DefaultRequestHandler(
    agent_executor=HelloWorldAgentExecutor("tester"),
    task_store=InMemoryTaskStore(),
)

server = A2AStarletteApplication(agent_card=agent_card, http_handler=request_handler)


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path)


@pytest.mark.skipif(
    not os.environ.get("TEST_GIT_REPO_URL"),
    reason="TEST_GIT_REPO_URL environment variable not set",
)
@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_dynamic_push_pull_client(transport, temp_dir):
    """
    End-to-end test for the A2A factory client over different transports.
    """
    endpoint = TRANSPORT_CONFIGS[transport]
    repo_url = os.environ.get("TEST_GIT_REPO_URL")
    foo_path = temp_dir / "github_foo"

    foo_dir = factory.create_directory(
        "GIT",
        repo_path=str(foo_path),
        holder_id="foo",
        remote_url=repo_url,
        auto_push=True,
    )

    bridge = factory.create_bridge(
        server,
        transport=factory.create_transport(transport, endpoint=endpoint, name="a/b/c"),
        topic=A2AProtocol.create_agent_topic(server.agent_card),
        agent_directory=foo_dir,
    )
    await bridge.start(blocking=False, push_to_directory=True)
    await asyncio.sleep(3)

    # bar agent, likely in another process or service, creates a directory
    bar_dir = factory.create_directory(
        "GIT",
        remote_url=repo_url,
        repo_path=str(foo_path),
        holder_id="bar",
        auto_push=True,
    )

    # bar agent uses the directory to list or search for agent records
    agent_records = await bar_dir.search_agent_records(query="hello_world", limit=1)

    # create
    bar_client_for_foo = await factory.create_client(
        "A2A",
        agent_topic=A2AProtocol.create_agent_topic(agent_records[0]),
        transport=factory.create_transport(transport, endpoint=endpoint, name="d/e/f"),
    )

    # Create A2A client
    assert bar_client_for_foo is not None, "Client was not created"

    print("\n=== Agent Information ===")
    print(f"Name: {bar_client_for_foo.agent_card.name}")

    await bridge.transport.close()
    await bar_client_for_foo.transport.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")
