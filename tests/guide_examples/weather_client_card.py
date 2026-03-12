# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Weather Client for card-driven bootstrap — adapted from
A2A_USAGE_GUIDE.md Example 2.

The client uses the same AgentCard (with ``additional_interfaces``) as the
server to ensure topic derivation matches.  ``add_a2a_card()`` is server-side
only; the client still uses ``factory.a2a(config).create(card)``.

Usage:
    uv run python tests/guide_examples/weather_client_card.py \
        --transport SLIM --endpoint http://localhost:46357
"""

import argparse
import asyncio
import uuid

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    TextPart,
)

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    A2AExperimentalServer,
)

# ---------------------------------------------------------------------------
# Constants (must match server)
# ---------------------------------------------------------------------------

SLIM_ENDPOINT = "slim://localhost:46357"
NATS_ENDPOINT = "nats://localhost:4222"

_PREFERRED_TRANSPORT: dict[str, str] = {
    "SLIM": InterfaceTransport.SLIM_PATTERNS,
    "NATS": InterfaceTransport.NATS_PATTERNS,
}

# Reconstruct the same agent_card as the server (needed for topic derivation)
skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)


def build_agent_card(transport_type: str) -> AgentCard:
    """Build the same AgentCard the server uses (for topic derivation)."""
    name = "default/default/Weather_Agent_1.0.0"

    return AgentCard(
        name="Weather Agent",
        description="An agent that provides weather reports",
        url="",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
        supportsAuthenticatedExtendedCard=False,
        preferredTransport=_PREFERRED_TRANSPORT[transport_type],
        additional_interfaces=[
            AgentInterface(
                transport=InterfaceTransport.SLIM_PATTERNS,
                url=f"{SLIM_ENDPOINT}/{name}",
            ),
            AgentInterface(
                transport=InterfaceTransport.NATS_PATTERNS,
                url=f"{NATS_ENDPOINT}/{name}",
            ),
        ],
    )


async def main(transport_type: str, endpoint: str):
    factory = AgntcyFactory()

    transport = factory.create_transport(
        transport_type,
        endpoint=endpoint,
        name="default/default/weather_client_card",
    )

    base_card = build_agent_card(transport_type)
    agent_card = A2AExperimentalServer.create_client_card(base_card, transport_type)

    config_kwargs = {}
    if transport_type == "SLIM":
        config_kwargs["slim_transport"] = transport
    elif transport_type == "NATS":
        config_kwargs["nats_transport"] = transport

    config = ClientConfig(**config_kwargs)
    client = await factory.a2a(config).create(agent_card)

    request = SendMessageRequest(
        id="request-001",
        params=MessageSendParams(
            message=Message(
                messageId=str(uuid.uuid4()),
                role=Role.user,
                parts=[
                    Part(
                        root=TextPart(text="Hello, Weather Agent, how is the weather?")
                    )
                ],
            ),
        ),
    )

    # Use send_message with the Message from the request
    output = ""
    async for event in client.send_message(request=request.params.message):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    output += part.root.text
                    print(part.root.text)
        else:
            task, _update = event
            if task.history:
                for msg in task.history:
                    if msg.role == Role.agent:
                        for part in msg.parts:
                            if isinstance(part.root, TextPart):
                                output += part.root.text
                                print(part.root.text)

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
        description="Weather Client (Card-Driven Bootstrap)"
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
