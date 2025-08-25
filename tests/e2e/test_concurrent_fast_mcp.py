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
  Concurrent test for multiple MCP clients over different transports.

  This test verifies the functionality of the MCP client by:
  1. Launching a test server for the specified transport.
  2. Creating transport instances and MCP clients concurrently.
  3. Listing available tools and validating the response for each client.
  4. Calling a tool and verifying the result for each client.

  **Parameters:**
  - `run_fast_mcp_server`: Fixture to launch the test server.
  - `transport`: Transport type to test (e.g., HTTP, WebSocket).

  **Raises:**
  - `AssertionError`: If any validation fails.
  - `Exception`: If an unexpected error occurs during the test.
  """

  endpoint = TRANSPORT_CONFIGS[transport]
  print(
    f"\n--- Starting test: test_concurrent_clients | Transport: {transport} | Endpoint: {endpoint} ---"
  )

  print("[setup] Launching test server...")
  run_fast_mcp_server(transport, endpoint)

  factory = AgntcyFactory()

  async def test_client_operations(user_id):
    print("[setup] Creating transport instance...")
    transport_instance = factory.create_transport(
      transport=transport, endpoint=endpoint
    )

    print(f"[test] Creating MCP client for user {user_id}...")
    mcp_client = await factory.create_client(
      "FastMCP",
      agent_topic=f"test_topic.mcp",
      transport=transport_instance,
      agent_url="http://localhost:8081/mcp",
    )

    async with mcp_client as client:
      try:
        print(f"[test] User {user_id} sending test message to list tools...")
        tools = await client.list_tools()
        print(f"[test] User {user_id} tools available:", tools)

        # Validate tools list
        assert tools is not None, f"User {user_id} tools list was None"
        assert len(list(tools)) > 0, f"User {user_id} has no tools available"

        print(f"[test] User {user_id} calling tool 'get_forecast'...")
        result = await client.call_tool(
          name="get_forecast",
          arguments={"location": "Colombia"},
        )

        # Expected response
        print(f"[test] User {user_id} tool response:", result)

        # Validate tool response
        assert result is not None, f"User {user_id} result was None"

      except AssertionError as ae:
        print(f"[error] User {user_id} assertion failed: {ae}")
        raise
      except Exception as e:
        print(f"[error] User {user_id} unexpected error during test execution: {e}")
        raise

  # Run tests concurrently for multiple clients or a single client
  user_ids = [1, 2, 3, 4, 5]  # Adjust this list to test multiple clients
  await asyncio.gather(
    *[test_client_operations(user_id) for user_id in user_ids]
  )

  print(f"=== ✅ Test passed for transport: {transport} ===\n")


async def test_client_operations(factory, transport_instance, user_id):
  mcp_client = await factory.create_client(
    "FastMCP",
    agent_topic=f"user_{user_id}_topic.mcp",
    transport=transport_instance,
    agent_url="http://localhost:8000/mcp",
  )

  async with mcp_client as client:
    try:
      print(f"[test] User {user_id} sending test message to list tools...")
      tools = await client.list_tools()
      print(f"[test] User {user_id} tools available:", tools)

      # Validate tools list
      assert tools is not None, f"User {user_id} tools list was None"
      assert len(list(tools)) > 0, f"User {user_id} has no tools available"

      print(f"[test] User {user_id} calling tool 'get_forecast'...")
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
      assert result is not None, f"User {user_id} result was None"
      assert result == expected_result, f"User {user_id} unexpected result: {result}"

    except AssertionError as ae:
      print(f"[error] User {user_id} assertion failed: {ae}")
      raise
    except Exception as e:
      print(f"[error] User {user_id} unexpected error during test execution: {e}")
      raise
