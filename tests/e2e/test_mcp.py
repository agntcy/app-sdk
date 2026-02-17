# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio

import pytest

from agntcy_app_sdk.factory import AgntcyFactory
from tests.e2e.conftest import TRANSPORT_CONFIGS

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# test_client — basic MCP tool call over each transport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "transport", list(TRANSPORT_CONFIGS.keys()), ids=lambda val: val
)
@pytest.mark.asyncio
async def test_client(run_mcp_server, transport):
    """Point-to-point MCP tool call over each transport."""
    if transport == "JSONRPC":
        pytest.skip("MCP not applicable for JSONRPC transport.")
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_client | {transport} | {endpoint} ---")

    run_mcp_server(transport, endpoint)
    await asyncio.sleep(1)

    factory = AgntcyFactory()
    transport_instance = factory.create_transport(
        transport=transport, endpoint=endpoint, name="default/default/mcp_client"
    )

    mcp_client = await factory.mcp().create_client(
        topic="mcp",
        transport=transport_instance,
    )

    async with mcp_client as client:
        tools = await client.list_tools()
        assert tools is not None, "Tools list was None"
        assert len(list(tools)) > 0, "No tools available"
        print(f"Tools: {tools}")

        result = await client.call_tool(
            name="get_forecast",
            arguments={"location": "Colombia"},
        )

        content_list = result.content
        assert isinstance(content_list, list) and len(content_list) > 0
        response = content_list[0].text
        assert response is not None, "Response was None"
        print(f"Tool result: {response}")

    if transport_instance:
        await transport_instance.close()

    print(f"=== ✅ test_client passed for {transport} ===\n")
