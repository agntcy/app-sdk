# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from uuid import uuid4
from typing import Any

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
            "parts": [
                {"kind": "text", "text": "Access Forbidden. Please check permissions."}
            ],
            "role": "agent",
        },
    }
