# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the transport-level :class:`Message`."""

from agntcy_app_sdk.semantic.message import Message


def test_str_payload_is_stored_as_utf8_bytes() -> None:
    message = Message(type="t", payload="héllo")

    assert message.payload == "héllo".encode("utf-8")


def test_bytes_payload_is_stored_unchanged() -> None:
    message = Message(type="t", payload=b"\x00\xff")

    assert message.payload == b"\x00\xff"


def test_str_payload_round_trips_as_bytes() -> None:
    message = Message(type="t", payload='{"k": "v"}', headers={"h": "1"})

    restored = Message.deserialize(message.serialize())

    assert restored.payload == b'{"k": "v"}'
    assert restored.type == "t"
    assert restored.headers == {"h": "1"}
