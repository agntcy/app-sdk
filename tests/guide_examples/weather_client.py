# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Client using Experimental Patterns (SLIM/NATS) — adapted from
A2A_USAGE_GUIDE.md Example 2.

Usage:
    uv run python tests/guide_examples/weather_client.py \
        --transport SLIM --endpoint http://localhost:46357
"""

import argparse
import asyncio

from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    Role,
    SendMessageRequest,
)

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    A2AExperimentalServer,
)

# Reconstruct the same agent_card as the server (needed for topic derivation)
skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)

base_agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
)


async def main(transport_type: str, endpoint: str):
    factory = AgntcyFactory()

    transport = factory.create_transport(
        transport_type,
        endpoint=endpoint,
        name="default/default/weather_client",
    )

    agent_card = A2AExperimentalServer.create_client_card(
        base_agent_card, transport_type
    )

    config_kwargs = {}
    if transport_type == "SLIM":
        config_kwargs["slim_transport"] = transport
    elif transport_type == "NATS":
        config_kwargs["nats_transport"] = transport

    config = ClientConfig(**config_kwargs)
    client = await factory.a2a(config).create(agent_card)

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

    await transport.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Weather Client (Experimental Patterns)"
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=["SLIM", "NATS"],
        default="SLIM",
        help="Transport type (default: SLIM)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="http://localhost:46357",
        help="Transport endpoint",
    )
    args = parser.parse_args()
    asyncio.run(main(args.transport, args.endpoint))
