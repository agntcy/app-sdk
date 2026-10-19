# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the NATS broadcast invite protocol.

The invite protocol allows multi-recipient broadcast without requiring agents
to pre-subscribe to a shared topic.  Instead, the client sends invite messages
to each recipient's unique name, waits for ACKs, then publishes the real
message on an ephemeral topic.
"""

import functools
from collections.abc import AsyncGenerator, Awaitable, Callable
from unittest.mock import AsyncMock, patch

import pytest
from nats.aio.msg import Msg

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.nats.transport import NatsTransport

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nats_msg(message: Message, *, reply: str = "") -> Msg:
    """Build a ``nats.aio.msg.Msg`` from a :class:`Message`."""
    return Msg(_client=AsyncMock(), data=message.serialize(), reply=reply)


def _make_transport(nc_mock: AsyncMock) -> NatsTransport:
    """Create a :class:`NatsTransport` with a mocked NATS client."""
    with patch.object(NatsTransport, "__init__", lambda self, **kw: None):
        transport = NatsTransport.__new__(NatsTransport)
    # Manually initialise the attributes that __init__ normally sets
    transport._nc = nc_mock
    transport.endpoint = "localhost:4222"
    transport._callback = None
    transport._callback_takes_publish_fn = False
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
async def test_handle_invite_subscribes_and_sends_ack() -> None:
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
async def test_handle_teardown_unsubscribes() -> None:
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
async def test_handle_teardown_unknown_topic_is_noop() -> None:
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
async def test_message_handler_intercepts_invite() -> None:
    """Invite messages are handled by ``_handle_invite`` and never reach the callback."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    callback = AsyncMock(return_value=Message(type="resp", payload=b"ok"))
    transport.set_callback(callback)

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
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_message_handler_intercepts_teardown() -> None:
    """Teardown messages are handled by ``_handle_teardown`` and never reach the callback."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    callback = AsyncMock(return_value=Message(type="resp", payload=b"ok"))
    transport.set_callback(callback)

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

    callback.assert_not_called()
    mock_sub.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_message_handler_passes_normal_messages() -> None:
    """Normal (non-invite) messages reach the callback and get a reply published."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    response = Message(type="response", payload=b"hello")
    callback = AsyncMock(return_value=response)
    transport.set_callback(callback)

    normal_msg = Message(type="request", payload=b"ping", reply_to="reply_topic")
    nats_msg = _make_nats_msg(normal_msg)

    await transport._message_handler(nats_msg)

    callback.assert_awaited_once()
    # The response should be published to the reply_to topic
    nc.publish.assert_called_once()
    assert nc.publish.call_args[0][0] == "reply_topic"


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [Message(type="response", payload=b"hello"), None])
async def test_message_handler_without_reply_to_publishes_nothing(
    response: Message | None,
) -> None:
    """One-way messages run the callback but are never answered."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    callback = AsyncMock(return_value=response)
    transport.set_callback(callback)

    await transport._message_handler(
        _make_nats_msg(Message(type="request", payload=b"ping"))
    )

    callback.assert_awaited_once()
    nc.publish.assert_not_called()


@pytest.mark.asyncio
async def test_message_handler_empty_response_sends_one_error_reply() -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    callback = AsyncMock(return_value=None)
    transport.set_callback(callback)

    await transport._message_handler(
        _make_nats_msg(Message(type="request", payload=b"ping", reply_to="inbox"))
    )

    nc.publish.assert_called_once()
    topic, data = nc.publish.call_args[0]
    assert topic == "inbox"
    reply = Message.deserialize(data)
    assert reply.type == "error"
    assert reply.payload == b"No response from handler"


async def _positional_only(message: Message) -> Message:
    return message


async def _with_publish_fn(
    message: Message,
    *,
    publish_fn: Callable[[Message], Awaitable[None]] | None = None,
) -> Message:
    assert publish_fn is not None
    return message


async def _with_kwargs(message: Message, **kwargs: object) -> Message:
    assert "publish_fn" in kwargs
    return message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [_positional_only, _with_publish_fn, _with_kwargs],
    ids=lambda h: h.__name__,
)
async def test_message_handler_passes_publish_fn_only_when_accepted(
    handler: Callable[..., Awaitable[Message]],
) -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    transport.set_callback(handler)

    await transport._message_handler(
        _make_nats_msg(Message(type="request", payload=b"ping", reply_to="inbox"))
    )

    nc.publish.assert_called_once()
    assert Message.deserialize(nc.publish.call_args[0][1]).payload == b"ping"


@pytest.mark.asyncio
async def test_message_handler_calls_wraps_wrapper_without_publish_fn() -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    calls: list[Message] = []

    @functools.wraps(_with_publish_fn)
    async def wrapper(message: Message) -> Message:
        calls.append(message)
        return message

    transport.set_callback(wrapper)

    await transport._message_handler(
        _make_nats_msg(Message(type="request", payload=b"ping", reply_to="inbox"))
    )

    assert len(calls) == 1
    nc.publish.assert_called_once()
    assert Message.deserialize(nc.publish.call_args[0][1]).payload == b"ping"


@pytest.mark.asyncio
async def test_message_handler_type_error_inside_handler_runs_it_once() -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    calls: list[bool] = []

    async def handler(
        message: Message,
        *,
        publish_fn: Callable[[Message], Awaitable[None]] | None = None,
    ) -> Message:
        calls.append(publish_fn is not None)
        raise TypeError("bug inside the handler")

    transport.set_callback(handler)

    with pytest.raises(TypeError, match="bug inside the handler"):
        await transport._message_handler(
            _make_nats_msg(Message(type="request", payload=b"ping", reply_to="inbox"))
        )

    assert calls == [True]


# ---------------------------------------------------------------------------
# gather_stream — single-recipient guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_stream_single_recipient_skips_invite() -> None:
    """With a single recipient, gather_stream publishes directly (no invite)."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    # Prepare a fake response that the subscriber will deliver
    response_msg = Message(type="response", payload=b"ok")

    # Make subscribe capture the callback so we can inject a response
    captured_cb: Callable[[Msg], Awaitable[None]] | None = None

    async def fake_subscribe(
        topic: str, cb: Callable[[Msg], Awaitable[None]] | None = None
    ) -> AsyncMock:
        nonlocal captured_cb
        captured_cb = cb
        sub = AsyncMock()
        return sub

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)

    async def fake_publish(topic: str, data: bytes) -> None:
        # After publish, simulate a response arriving on the reply subscription
        if captured_cb is not None:
            fake_nats_msg = _make_nats_msg(response_msg)
            await captured_cb(fake_nats_msg)

    nc.publish = AsyncMock(side_effect=fake_publish)

    msg = Message(type="request", payload=b"test")
    results: list[Message] = []
    with patch(
        "agntcy_app_sdk.common.auth.is_identity_auth_enabled",
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


@pytest.mark.asyncio
async def test_gather_stream_keeps_caller_authorization_and_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    captured_cb: Callable[[Msg], Awaitable[None]] | None = None
    published: list[Message] = []

    async def fake_subscribe(
        topic: str, cb: Callable[[Msg], Awaitable[None]] | None = None
    ) -> AsyncMock:
        nonlocal captured_cb
        captured_cb = cb
        return AsyncMock()

    async def fake_publish(topic: str, data: bytes) -> None:
        published.append(Message.deserialize(data))
        if captured_cb is not None:
            await captured_cb(_make_nats_msg(Message(type="response", payload=b"ok")))

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)
    nc.publish = AsyncMock(side_effect=fake_publish)
    monkeypatch.setenv("IDENTITY_AUTH_ENABLED", "true")
    monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
    get_token = AsyncMock(return_value="tok")
    monkeypatch.setattr("agntcy_app_sdk.common.auth.get_access_token", get_token)
    msg = Message(
        type="request", payload=b"test", headers={"authorization": "Bearer mine"}
    )

    _ = [
        r
        async for r in transport.gather_stream(
            "topic", msg, ["single_recipient"], timeout=5, message_limit=1
        )
    ]

    assert published[0].headers["authorization"] == "Bearer mine"
    assert "Authorization" not in published[0].headers
    assert msg.headers == {"authorization": "Bearer mine"}
    assert msg.reply_to is None
    get_token.assert_not_awaited()


# ---------------------------------------------------------------------------
# gather_stream — multi-recipient invite protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gather_stream_multi_recipient_uses_invite() -> None:
    """With multiple recipients, gather_stream uses the invite protocol."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)

    response_msg = Message(type="response", payload=b"ok")
    ack_msg = Message(
        type="ack", payload=b"", headers={"x-nats-invite-type": "invite_ack"}
    )

    captured_cbs: dict[str, Callable[[Msg], Awaitable[None]]] = {}

    async def fake_subscribe(
        topic: str, cb: Callable[[Msg], Awaitable[None]] | None = None
    ) -> AsyncMock:
        assert cb is not None
        captured_cbs[topic] = cb
        sub = AsyncMock()
        return sub

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)

    publish_calls: list[tuple[str, bytes]] = []

    async def fake_publish(topic: str, data: bytes) -> None:
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
    results: list[Message] = []
    with patch(
        "agntcy_app_sdk.common.auth.is_identity_auth_enabled",
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


def _wire_invite_protocol(
    nc: AsyncMock, *, fail_on_request: bool = False
) -> list[Message]:
    """ACK every invite and answer the broadcast twice; return what was published."""
    callbacks: dict[str, Callable[[Msg], Awaitable[None]]] = {}
    published: list[Message] = []
    ack = Message(type="ack", payload=b"", headers={"x-nats-invite-type": "invite_ack"})

    async def fake_subscribe(
        topic: str, cb: Callable[[Msg], Awaitable[None]] | None = None
    ) -> AsyncMock:
        assert cb is not None
        callbacks[topic] = cb
        return AsyncMock()

    async def fake_publish(topic: str, data: bytes) -> None:
        msg = Message.deserialize(data)
        published.append(msg)
        if msg.headers.get("x-nats-invite-type") == "invite":
            await callbacks[msg.headers["x-nats-ack-topic"]](_make_nats_msg(ack))
        elif msg.type == "request":
            if fail_on_request:
                raise ConnectionError("publish failed")
            assert msg.reply_to is not None
            for _ in range(2):
                await callbacks[msg.reply_to](
                    _make_nats_msg(Message(type="response", payload=b"ok"))
                )

    nc.subscribe = AsyncMock(side_effect=fake_subscribe)
    nc.publish = AsyncMock(side_effect=fake_publish)
    return published


def _teardowns(published: list[Message]) -> list[Message]:
    return [m for m in published if m.headers.get("x-nats-invite-type") == "teardown"]


@pytest.mark.asyncio
async def test_gather_stream_sends_teardown_when_caller_stops_early() -> None:
    """The A2A client breaks out once every recipient replied; recipients must
    still be told to drop their ephemeral subscription."""
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    published = _wire_invite_protocol(nc)

    with patch(
        "agntcy_app_sdk.common.auth.is_identity_auth_enabled",
        return_value=False,
    ):
        stream = transport.gather_stream(
            "broadcast", Message(type="request", payload=b"x"), ["a", "b"], timeout=5
        )
        assert isinstance(stream, AsyncGenerator)
        received = 0
        async for _ in stream:
            received += 1
            if received == 2:
                break
        await stream.aclose()

    assert len(_teardowns(published)) == 1


@pytest.mark.asyncio
async def test_gather_stream_sends_teardown_when_publish_fails() -> None:
    nc = _make_nc_mock()
    transport = _make_transport(nc)
    published = _wire_invite_protocol(nc, fail_on_request=True)

    with patch(
        "agntcy_app_sdk.common.auth.is_identity_auth_enabled",
        return_value=False,
    ):
        with pytest.raises(ConnectionError, match="publish failed"):
            async for _ in transport.gather_stream(
                "broadcast",
                Message(type="request", payload=b"x"),
                ["a", "b"],
                timeout=5,
            ):
                pass

    assert len(_teardowns(published)) == 1


# ---------------------------------------------------------------------------
# close() — ephemeral subscription cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_cleans_up_ephemeral_subs() -> None:
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
