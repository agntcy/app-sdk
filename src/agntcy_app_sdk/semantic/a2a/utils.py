# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import httpx
import json
from uuid import uuid4
from typing import Any

from a2a.client import A2AClient, A2ACardResolver
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH
from a2a.types import (
    AgentCard,
)

from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)



def message_translator(
    request: dict[str, Any], headers: dict[str, Any] | None = None
) -> Message:
    """
    Translate an A2A request into the internal Message object.
    """
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise ValueError("Headers must be a dictionary")

    message = Message(
        type="A2ARequest",
        payload=json.dumps(request),
        route_path="/",  # json-rpc path
        method="POST",  # A2A json-rpc will always use POST
        headers=headers,
    )
    return message


async def get_client_from_agent_card_topic(
    topic: str, transport: BaseTransport
) -> A2AClient:
    """
    Create an A2A client from the agent card topic, bypassing all need for a URL.
    """
    logger.info(f"Getting agent card from topic {topic}")

    request = Message(
        type="A2ARequest",
        payload=json.dumps({"path": AGENT_CARD_WELL_KNOWN_PATH, "method": "GET"}),
        route_path=AGENT_CARD_WELL_KNOWN_PATH,
        method="GET",
    )
    response = await transport.request(topic, request)

    response.payload = json.loads(response.payload.decode("utf-8"))
    card = AgentCard.model_validate(response.payload)

    cl = A2AClient(
        agent_card=card,
        httpx_client=None,  # Set the httpx_client to None so it will use the overridden version of _send_request() below
        url=None,
    )
    cl.agent_card = card
    return cl


async def get_client_from_agent_card_url(
    httpx_client: httpx.AsyncClient,
    base_url: str,
    http_kwargs: dict[str, Any] | None = None,
) -> A2AClient:
    """
    Replacement for removed get_client_from_agent_card_url().
    Tries both agent-card.json (v0.3.0) and legacy agent.json.
    """
    agent_card: AgentCard = await A2ACardResolver(
        httpx_client,
        base_url=base_url,
        agent_card_path=AGENT_CARD_WELL_KNOWN_PATH,
    ).get_agent_card(http_kwargs=http_kwargs)

    return A2AClient(httpx_client=httpx_client, agent_card=agent_card)

def get_identity_auth_error() -> dict[str, Any]:
    """
    Generate a standard identity authentication error response.
    """
    return {
        "id": str(uuid4()),
        "jsonrpc": "2.0",
        "result": {
            "kind": "message",
            "messageId": str(uuid4()),
            "metadata": {"name": "None"},
            "parts": [{"kind": "text", "text": "Access Forbidden. Please check permissions."}],
            "role": "agent"
        }
    }
