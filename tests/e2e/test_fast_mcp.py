# Copyright AGNTCY Contributors
# SPDX-License-Identifier: Apache-2.0

import pytest
from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize("transport", TRANSPORT_CONFIGS.keys(), ids=str)
@pytest.mark.asyncio
async def test_client(run_fast_mcp_server, transport):
  """
  End-to-end test for the MCP client using various transports.
  Verifies tools, resources, prompts, and completions functionality.
  """
  endpoint = TRANSPORT_CONFIGS[transport]
  print(f"\n=== Running test for transport: {transport} | endpoint: {endpoint} ===")

  run_fast_mcp_server(transport, endpoint)
  factory = AgntcyFactory()

  transport_instance = factory.create_transport(transport=transport, endpoint=endpoint)

  mcp_client = await factory.create_client(
    "FastMCP",
    agent_topic="test_topic.mcp",
    transport=transport_instance,
    agent_url="http://localhost:8081/mcp",
  )

  try:
    async with mcp_client as client:

      # --- Tools ---
      tools = await client.list_tools()
      assert tools, "Expected at least one tool"
      print("[tools] list_tools:", tools)

      result = await client.call_tool("get_forecast", {"location": "Colombia"})
      expected = {
        'content': [{'type': 'text', 'text': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'}],
        'structuredContent': {'result': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'},
        'isError': False,
      }
      assert result == expected, f"[tools] Unexpected tool result: {result}"

      # --- Resources ---
      resources = await client.list_resources()
      assert resources is not None, "Expected resources to be a list"

      templates = await client.list_resource_templates()
      assert templates is not None, "Expected templates to be a list"

    print(f"=== ✅ Test passed for transport: {transport} ===\n")

  finally:
    print("[teardown] Closing transport...")
    await transport_instance.close()
