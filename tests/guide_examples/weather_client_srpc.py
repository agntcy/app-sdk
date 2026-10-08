# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Client using SlimRPC — adapted from A2A_USAGE_GUIDE.md Example 1.

Usage:
    uv run python tests/guide_examples/weather_client_srpc.py \
        --endpoint http://localhost:46357 \
        --agent-name default/default/weather-agent
"""

import argparse
import asyncio

from a2a.client import minimal_agent_card
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest
from slima2a import setup_slim_client
from slima2a.client_transport import slimrpc_channel_factory

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig


async def main(endpoint: str, agent_name: str):
    factory = AgntcyFactory()

    # 1. Set up the low-level SLIM connection (needed for slimrpc channel)
    _service, slim_app, _local_name, conn_id = await setup_slim_client(
        namespace="default",
        group="default",
        name="weather_client",
        slim_url=endpoint,
    )

    # 2. Build a ClientConfig with slimrpc enabled
    config = ClientConfig(
        slimrpc_channel_factory=slimrpc_channel_factory(slim_app, conn_id),
    )

    # 3. Create client — transport negotiation happens inside create()
    card = minimal_agent_card(agent_name, ["slimrpc"])
    client = await factory.a2a(config).create(card)

    # 4. Send a message
    request = SendMessageRequest(
        message=new_text_message(
            "Hello, Weather Agent, how is the weather?", role=Role.ROLE_USER
        )
    )

    # send_message yields StreamResponse events (message / task / status updates)
    output = ""
    async for event in client.send_message(request):
        text = get_stream_response_text(event)
        if text:
            output += text
            print(text)

    if not output:
        print("ERROR: No response received")
        exit(1)
    if "sunny" not in output.lower() and "75" not in output:
        print(f"ERROR: Unexpected response: {output}")
        exit(1)
    print(f"SUCCESS: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Weather Client (SlimRPC)")
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:46357",
        help="SLIM endpoint",
    )
    parser.add_argument(
        "--agent-name",
        type=str,
        default="default/default/weather-agent",
        help="Agent identity on SLIM",
    )
    args = parser.parse_args()
    asyncio.run(main(args.endpoint, args.agent_name))
