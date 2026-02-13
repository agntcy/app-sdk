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
    """Concurrent FastMCP clients over each transport.

    Launches a server, then fires 5 clients concurrently — each listing
    tools and calling ``get_forecast``.
    """
    endpoint = TRANSPORT_CONFIGS[transport]
    print(f"\n--- test_concurrent_client | {transport} | {endpoint} ---")

    run_fast_mcp_server(transport, endpoint)
    await asyncio.sleep(1)  # let server subscribe before clients connect

    factory = AgntcyFactory()
    transport_instances = []

    async def run_client(user_id: int) -> None:
        # Small stagger to avoid session contention on SLIM
        await asyncio.sleep(0.1 * user_id)

        transport_instance = factory.create_transport(
            transport=transport,
            endpoint=endpoint,
            name="default/default/fastmcp_client",
        )
        transport_instances.append(transport_instance)

        mcp_client = await factory.create_client(
            "FastMCP",
            agent_topic="fastmcp",
            transport=transport_instance,
            agent_url="http://localhost:8081/mcp",
        )

        async with mcp_client as client:
            tools = await client.list_tools()
            assert tools is not None, f"User {user_id} tools list was None"
            assert len(list(tools)) > 0, f"User {user_id} has no tools available"

            result = await client.call_tool(
                name="get_forecast",
                arguments={"location": "Colombia"},
            )
            assert result is not None, f"User {user_id} result was None"
            print(f"User {user_id} ✅")

    user_ids = [1, 2, 3, 4, 5]
    await asyncio.gather(*[run_client(uid) for uid in user_ids])

    # Clean up transports
    for t in transport_instances:
        await t.close()

    print(f"=== ✅ test_concurrent_client passed for {transport} ===\n")
