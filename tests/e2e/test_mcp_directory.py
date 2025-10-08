# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS
from agntcy_app_sdk.discovery.mcp.agent_directory import MCPAgentDirectoryClient
from a2a.server.apps import A2AStarletteApplication
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import pytest

pytest_plugins = "pytest_asyncio"

skill = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)

agent_card = AgentCard(
    name="Hello-World-Agent",
    description="Just a hello world agent",
    url="http://localhost:9999/",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],  # Only the basic skill for the public card
    supportsAuthenticatedExtendedCard=False,
)


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_publish_record(transport):
    """
    End-to-end test for the A2A factory client over different transports.
    """

    # Get the endpoint inside the test using the transport name as a key
    endpoint = TRANSPORT_CONFIGS[transport]

    factory = AgntcyFactory()
    transport_instance = factory.create_transport(
        transport=transport,
        endpoint=endpoint,
        name="default/default/mcp_directory_client",
    )

    # Create the MCP agent directory client
    mcp_directory_client = MCPAgentDirectoryClient(transport=transport_instance)

    # get a generic A2A server
    server = A2AStarletteApplication(agent_card=agent_card, http_handler=None)

    # simulate serving this agent with a transport bridge
    bridge = factory.create_bridge(
        server,
        transport=transport_instance,
        topic="my-address",
        agent_directory=mcp_directory_client,
    )
    await bridge.start(publish_to_directory=True, blocking=True)

    print(
        f"\n--- Starting test: test_publish | Transport: {transport} | Endpoint: {endpoint} ---"
    )
