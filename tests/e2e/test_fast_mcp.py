# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS
import pytest
import asyncio

pytest_plugins = "pytest_asyncio"


@pytest.mark.parametrize(
  "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_fast_mcp_server, transport):
  """
  End-to-end test for the A2A factory client over different transports.
  """

  endpoint = TRANSPORT_CONFIGS[transport]
  print(
    f"\n--- Starting test: test_client | Transport: {transport} | Endpoint: {endpoint} ---"
  )

  print("[setup] Launching test server...")
  run_fast_mcp_server(transport, endpoint)

  factory = AgntcyFactory()
  transport_instance = factory.create_transport(
    transport=transport, endpoint=endpoint
  )

  print("[test] Creating MCP client...")
  mcp_client = factory.create_client(
    "FastMCP",
    agent_topic="test_topic.mcp",
    transport=transport_instance,
  )

  async with mcp_client as client:
    try:
      print("[test] Sending test message...")
      tools = await client.list_tools()
      print("[test] Tools available:", tools)
      assert tools is not None, "Tools list was None"
      assert len(list(tools)) > 0, "No tools available in the list"

      # result = await client.call_tool(
      #   name="get_forecast",
      #   arguments={"location": "Colombia"},
      # )
      # print(f"Tool call result: {result}")
      #
      # response = ""
      # if hasattr(result, "__aiter__"):
      #   async for chunk in result:
      #     delta = chunk.choices[0].delta
      #     response += delta.content or ""
      # else:
      #   content_list = result.content
      #   if isinstance(content_list, list) and len(content_list) > 0:
      #     response = content_list[0].text
      #   else:
      #     response = "No content returned from tool."
      #
      # assert response is not None, "Response was None"
      # print(f"[debug] Raw response: {response}")

    except Exception as e:
      print(f"[error] Failed to send message: {e}")
      raise

    finally:
      if transport_instance:
        print("[teardown] Closing transport...")
        await transport_instance.close()

    print(f"=== ✅ Test passed for transport: {transport} ===\n")