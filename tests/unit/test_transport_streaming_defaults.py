# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the default streaming methods of the transport mixins.

An unsupported streaming method must raise ``NotImplementedError`` when the
caller iterates it with ``async for``, not ``TypeError`` from iterating a
coroutine, so callers can catch it and fall back.
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from a2a.helpers import new_text_message
from a2a.types import AgentCard, AgentInterface, Role, SendMessageRequest

from agntcy_app_sdk.semantic.a2a.client.transports import PatternsClientTransport
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import (
    FanOutMixin,
    GroupChatMixin,
    PointToPointMixin,
)
from agntcy_app_sdk.transport.nats.transport import NatsTransport

pytest_plugins = "pytest_asyncio"


def _transport() -> NatsTransport:
    return NatsTransport(endpoint="nats://localhost:4222")


def _message() -> Message:
    return Message(type="test", payload=b"hello")


_DEFAULTS: dict[str, Callable[[NatsTransport], AsyncIterator[Message]]] = {
    "request_stream": lambda t: PointToPointMixin.request_stream(t, "r", _message()),
    "gather_stream": lambda t: FanOutMixin.gather_stream(t, "topic", _message(), ["r"]),
    "start_streaming_conversation": lambda t: (
        GroupChatMixin.start_streaming_conversation(
            t, "channel", ["r"], _message(), "done"
        )
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(_DEFAULTS))
async def test_base_default_raises_not_implemented_on_iteration(name: str) -> None:
    stream = _DEFAULTS[name](_transport())

    with pytest.raises(NotImplementedError):
        async for _ in stream:
            pass


@pytest.mark.asyncio
async def test_nats_streaming_conversation_raises_not_implemented() -> None:
    stream = _transport().start_streaming_conversation(
        "channel", ["r"], _message(), "done"
    )

    with pytest.raises(NotImplementedError):
        async for _ in stream:
            pass


@pytest.mark.asyncio
async def test_patterns_streaming_falls_back_on_base_default() -> None:
    """``send_message_streaming`` falls back to unary on the real base default."""
    nats = _transport()

    def _base_request_stream(
        recipient: str, message: Message, **kwargs: Any
    ) -> AsyncIterator[Message]:
        return PointToPointMixin.request_stream(nats, recipient, message, **kwargs)

    reply = MagicMock()
    reply.payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "message": {
                    "messageId": "m-1",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "Fallback"}],
                }
            },
        }
    ).encode("utf-8")
    reply.headers = {}

    mock_transport = MagicMock()
    mock_transport.request = AsyncMock(return_value=reply)
    mock_transport.request_stream = MagicMock(side_effect=_base_request_stream)
    card = AgentCard(
        name="test-agent",
        version="1.0",
        description="Test agent",
        supported_interfaces=[
            AgentInterface(protocol_binding="JSONRPC", url="http://localhost:8080")
        ],
    )
    pct = PatternsClientTransport(mock_transport, card, "test_topic")
    request = SendMessageRequest(message=new_text_message("Hi", role=Role.ROLE_USER))

    events = [e async for e in pct.send_message_streaming(request)]

    assert len(events) == 1
    assert events[0].message.parts[0].text == "Fallback"
    mock_transport.request.assert_awaited_once()
