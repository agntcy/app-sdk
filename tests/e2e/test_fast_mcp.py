# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# test_client — FastMCP tool call, resources, and templates over each transport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_fast_mcp_server, transport):
    """FastMCP tool call with resource/template listing over each transport."""
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_client | {transport} | {endpoint} ---")

    run_fast_mcp_server(transport, endpoint)
    await asyncio.sleep(1)

    factory = AgntcyFactory()
    transport_instance = factory.create_transport(
        transport=transport, endpoint=endpoint, name="default/default/fastmcp_client"
    )

    mcp_client = await factory.create_client(
        "FastMCP",
        agent_topic="fastmcp",
        transport=transport_instance,
        agent_url="http://localhost:8081/mcp",
    )

    async with mcp_client as client:
        # Tools
        tools = await client.list_tools()
        assert tools, "Expected at least one tool"
        print(f"Tools: {tools}")

        result = await client.call_tool("get_forecast", {"location": "Colombia"})
        expected = {
            "content": [
                {
                    "type": "text",
                    "text": "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n",
                }
            ],
            "structuredContent": {
                "result": "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n"
            },
            "isError": False,
        }
        print(f"Tool result: {result}")
        assert result == expected, f"Unexpected tool result: {result}"

        # Resources
        resources = await client.list_resources()
        assert resources is not None, "Expected resources to be a list"

        templates = await client.list_resource_templates()
        assert templates is not None, "Expected templates to be a list"

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_client passed for {transport} ===\n")
