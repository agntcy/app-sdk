# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import pytest
from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize(
  "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_fast_mcp_server, transport):
  """
  End-to-end test for the A2A factory client over different transports.

  This test verifies the functionality of the MCP client by:
  1. Launching a test server for the specified transport.
  2. Creating a transport instance and MCP client.
  3. Listing available tools and validating the response.
  4. Calling a tool and verifying the result.

  **Parameters:**
  - `run_fast_mcp_server`: Fixture to launch the test server.
  - `transport`: Transport type to test (e.g., HTTP, WebSocket).

  **Raises:**
  - `AssertionError`: If any validation fails.
  - `Exception`: If an unexpected error occurs during the test.
  """

  endpoint = TRANSPORT_CONFIGS[transport]
  print(
    f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
  )

  try:
    print("[setup] Launching test server...")
    run_fast_mcp_server(transport, endpoint)

    factory = AgntcyFactory()

    print("[setup] Creating transport instance...")
    transport_instance = factory.create_transport(
      transport=transport, endpoint=endpoint
    )

    print("[test] Creating MCP client...")
    mcp_client = await factory.create_client(
      "FastMCP",
      agent_topic="test_topic.mcp",
      transport=transport_instance,
      agent_url="http://localhost:8000/mcp",
    )

    async with mcp_client as client:
      try:
        print("[test] Sending test message to list tools...")
        tools = await client.list_tools()
        print("[test] Tools available:", tools)

        # Validate tools list
        assert tools is not None, "Tools list was None"
        assert len(list(tools)) > 0, "No tools available in the list"

        print("[test] Calling tool 'get_forecast'...")
        result = await client.call_tool(
          name="get_forecast",
          arguments={"location": "Colombia"},
        )

        # Expected response
        expected_result = {
          'content': [{'type': 'text', 'text': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'}],
          'structuredContent': {'result': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'},
          'isError': False
        }

        # Validate tool response
        assert result is not None, "Result was None"
        assert result == expected_result, f"Unexpected result: {result}"

      except AssertionError as ae:
        print(f"[error] Assertion failed: {ae}")
        raise
      except Exception as e:
        print(f"[error] Unexpected error during test execution: {e}")
        raise

      finally:
        if transport_instance:
          print("[teardown] Closing transport...")
          await transport_instance.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")

  except Exception as e:
    print(f"[error] Test setup or execution failed: {e}")
    raise