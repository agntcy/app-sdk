# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the NATS broadcast invite protocol.

The invite protocol allows multi-recipient broadcast without requiring agents
to pre-subscribe to a shared topic.  Instead, the client sends invite messages
to each recipient's unique name, waits for ACKs, then publishes the real
message on an ephemeral topic.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.nats.transport import NatsTransport

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nats_msg(message: Message, *, reply: str = "") -> SimpleNamespace:
    """Build a fake ``nats.aio.msg.Msg``-like object from a :class:`Message`."""
    return SimpleNamespace(data=message.serialize(), reply=reply)


def _make_transport(nc_mock: AsyncMock) -> NatsTransport:
    """Create a :class:`NatsTransport` with a mocked NATS client."""
    with patch.object(NatsTransport, "__init__", lambda self, **kw: None):
        transport = NatsTransport.__new__(NatsTransport)
    # Manually initialise the attributes that __init__ normally sets
    transport._nc = nc_mock
    transport.endpoint = "localhost:4222"
    transport._callback = None
    transport.subscriptions = []
    transport._ephemeral_subs = {}
    return transport


def _make_nc_mock() -> AsyncMock:
    """Return an ``AsyncMock`` that behaves like ``nats.aio.client.Client``."""
    nc = AsyncMock()
    nc.is_connected = True
    # Each call to subscribe returns a distinct mock subscription
    nc.subscribe = AsyncMock(side_effect=lambda *a, **kw: AsyncMock())
    nc.publish = AsyncMock()
    return nc


# ---------------------------------------------------------------------------
# _handle_invite
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_invite_subscribes_and_sends_ack():
    """``_handle_invite`` subscribes to the broadcast topic and publishes an ACK."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    invite_msg = Message(
        type="invite",
        payload=b"",
        headers={
            "x-nats-invite-type": "invite",
            "x-nats-broadcast-topic": "ephemeral123",
            "x-nats-ack-topic": "ack456",
        },
    )

    await transport._handle_invite(invite_msg)

    # Must subscribe to the ephemeral broadcast topic
    nc.subscribe.assert_called_once()
    call_args = nc.subscribe.call_args
    assert call_args[0][0] == "ephemeral123"

    # Must store the subscription
    assert "ephemeral123" in transport._ephemeral_subs

    # Must publish an ACK to ack_topic (via send → nc.publish)
    nc.publish.assert_called_once()
    published_topic = nc.publish.call_args[0][0]
    assert published_topic == "ack456"

    # The ACK payload should deserialize to a Message with invite_ack header
    ack_data = nc.publish.call_args[0][1]
    ack_msg = Message.deserialize(ack_data)
    assert ack_msg.headers.get("x-nats-invite-type") == "invite_ack"


# ---------------------------------------------------------------------------
# _handle_teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_teardown_unsubscribes():
    """``_handle_teardown`` removes and unsubscribes the ephemeral sub."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    # Simulate a previously stored ephemeral subscription
    mock_sub = AsyncMock()
    transport._ephemeral_subs["ephemeral123"] = mock_sub

    teardown_msg = Message(
        type="teardown",
        payload=b"",
        headers={
            "x-nats-invite-type": "teardown",
            "x-nats-broadcast-topic": "ephemeral123",
        },
    )

    await transport._handle_teardown(teardown_msg)

    mock_sub.unsubscribe.assert_awaited_once()
    assert "ephemeral123" not in transport._ephemeral_subs


@pytest.mark.asyncio
async def test_handle_teardown_unknown_topic_is_noop():
    """``_handle_teardown`` is a no-op when the topic is not tracked."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    teardown_msg = Message(
        type="teardown",
        payload=b"",
        headers={
            "x-nats-invite-type": "teardown",
            "x-nats-broadcast-topic": "unknown_topic",
        },
    )

    # Should not raise
    await transport._handle_teardown(teardown_msg)
    assert transport._ephemeral_subs == {}


# ---------------------------------------------------------------------------
# _message_handler — invite / teardown interception
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_message_handler_intercepts_invite():
    """Invite messages are handled by ``_handle_invite`` and never reach the callback."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    transport._callback = AsyncMock(return_value=Message(type="resp", payload=b"ok"))

    invite_msg = Message(
        type="invite",
        payload=b"",
        headers={
            "x-nats-invite-type": "invite",
            "x-nats-broadcast-topic": "eph",
            "x-nats-ack-topic": "ack",
        },
    )
    nats_msg = _make_nats_msg(invite_msg)

    await transport._message_handler(nats_msg)

    # Callback must NOT have been called
    transport._callback.assert_not_called()


@pytest.mark.asyncio
async def test_message_handler_intercepts_teardown():
    """Teardown messages are handled by ``_handle_teardown`` and never reach the callback."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    transport._callback = AsyncMock(return_value=Message(type="resp", payload=b"ok"))

    # Pre-register an ephemeral sub so teardown has something to remove
    mock_sub = AsyncMock()
    transport._ephemeral_subs["eph"] = mock_sub

    teardown_msg = Message(
        type="teardown",
        payload=b"",
        headers={
            "x-nats-invite-type": "teardown",
            "x-nats-broadcast-topic": "eph",
        },
    )
    nats_msg = _make_nats_msg(teardown_msg)

    await transport._message_handler(nats_msg)

    transport._callback.assert_not_called()
    mock_sub.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_handler_passes_normal_messages():
    """Normal (non-invite) messages reach the callback and get a reply published."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    response = Message(type="response", payload=b"hello")
    transport._callback = AsyncMock(return_value=response)

    normal_msg = Message(type="request", payload=b"ping", reply_to="reply_topic")
    nats_msg = _make_nats_msg(normal_msg)

    await transport._message_handler(nats_msg)

    transport._callback.assert_awaited_once()
    # The response should be published to the reply_to topic
    nc.publish.assert_called_once()
    assert nc.publish.call_args[0][0] == "reply_topic"


# ---------------------------------------------------------------------------
# gather_stream — single-recipient guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_stream_single_recipient_skips_invite():
    """With a single recipient, gather_stream publishes directly (no invite)."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    # Prepare a fake response that the subscriber will deliver
    response_msg = Message(type="response", payload=b"ok")

    # Make subscribe capture the callback so we can inject a response
    captured_cb = None

    async def fake_subscribe(topic, cb=None):
        nonlocal captured_cb
        captured_cb = cb
        sub = AsyncMock()
        return sub

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)

    async def fake_publish(topic, data):
        # After publish, simulate a response arriving on the reply subscription
        if captured_cb is not None:
            fake_nats_msg = _make_nats_msg(response_msg)
            await captured_cb(fake_nats_msg)

    nc.publish = AsyncMock(side_effect=fake_publish)

    msg = Message(type="request", payload=b"test")
    results = []
    with patch(
        "agntcy_app_sdk.transport.nats.transport.is_identity_auth_enabled",
        return_value=False,
    ):
        async for resp in transport.gather_stream(
            "topic",
            msg,
            ["single_recipient"],
            timeout=5,
            message_limit=1,
        ):
            results.append(resp)

    assert len(results) == 1
    assert results[0].type == "response"

    # There should be exactly 1 subscribe call (for the reply topic)
    # and no invite-related publishes
    assert nc.subscribe.call_count == 1


# ---------------------------------------------------------------------------
# gather_stream — multi-recipient invite protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_stream_multi_recipient_uses_invite():
    """With multiple recipients, gather_stream uses the invite protocol."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    response_msg = Message(type="response", payload=b"ok")
    ack_msg = Message(
        type="ack", payload=b"", headers={"x-nats-invite-type": "invite_ack"}
    )

    captured_cbs: dict[str, AsyncMock] = {}

    async def fake_subscribe(topic, cb=None):
        captured_cbs[topic] = cb
        sub = AsyncMock()
        return sub

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)

    publish_calls = []

    async def fake_publish(topic, data):
        publish_calls.append((topic, data))
        msg = Message.deserialize(data)

        # When an invite is published to a recipient, simulate an ACK
        if msg.headers.get("x-nats-invite-type") == "invite":
            ack_topic_val = msg.headers["x-nats-ack-topic"]
            if ack_topic_val in captured_cbs:
                await captured_cbs[ack_topic_val](_make_nats_msg(ack_msg))

        # When the real message is published to the ephemeral topic,
        # simulate responses arriving on the reply topic
        if msg.reply_to and msg.type == "request":
            reply_topic_val = msg.reply_to
            if reply_topic_val in captured_cbs:
                for _ in range(2):
                    await captured_cbs[reply_topic_val](_make_nats_msg(response_msg))

    nc.publish = AsyncMock(side_effect=fake_publish)

    msg = Message(type="request", payload=b"test")
    results = []
    with patch(
        "agntcy_app_sdk.transport.nats.transport.is_identity_auth_enabled",
        return_value=False,
    ):
        async for resp in transport.gather_stream(
            "broadcast",
            msg,
            ["agent1", "agent2"],
            timeout=5,
            message_limit=2,
        ):
            results.append(resp)

    assert len(results) == 2

    # Should have 2 subscribe calls: reply_topic and ack_topic
    assert nc.subscribe.call_count == 2

    # Verify invite messages were sent to each recipient
    invite_publishes = [
        (t, Message.deserialize(d))
        for t, d in publish_calls
        if Message.deserialize(d).headers.get("x-nats-invite-type") == "invite"
    ]
    assert len(invite_publishes) == 2

    # Verify teardown was sent
    teardown_publishes = [
        (t, Message.deserialize(d))
        for t, d in publish_calls
        if Message.deserialize(d).headers.get("x-nats-invite-type") == "teardown"
    ]
    assert len(teardown_publishes) == 1


# ---------------------------------------------------------------------------
# close() — ephemeral subscription cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_cleans_up_ephemeral_subs():
    """``close()`` unsubscribes all ephemeral subscriptions before draining."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    sub1 = AsyncMock()
    sub2 = AsyncMock()
    transport._ephemeral_subs = {"topic_a": sub1, "topic_b": sub2}

    await transport.close()

    sub1.unsubscribe.assert_awaited_once()
    sub2.unsubscribe.assert_awaited_once()
    assert transport._ephemeral_subs == {}
    nc.drain.assert_awaited_once()
    nc.close.assert_awaited_once()
