# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``agntcy_app_sdk.semantic.a2a.card_utils``.

a2a-sdk 1.x models transports as the ordered ``AgentCard.supported_interfaces``
list; these helpers are the SDK's thin API over it.
"""

from a2a.types import AgentCard, AgentInterface

from agntcy_app_sdk.semantic.a2a.card_utils import (
    add_interface,
    clone_card,
    find_interface,
    get_card_url,
    preferred_transport,
    wire_binding,
)


def _card(*interfaces: tuple[str, str]) -> AgentCard:
    return AgentCard(
        name="agent",
        supported_interfaces=[
            AgentInterface(protocol_binding=b, url=u) for b, u in interfaces
        ],
    )


def _pairs(card: AgentCard) -> list[tuple[str, str]]:
    return [(i.protocol_binding, i.url) for i in card.supported_interfaces]


class TestWireBinding:
    def test_known_upstream_bindings_use_upstream_casing(self):
        assert wire_binding("jsonrpc") == "JSONRPC"
        assert wire_binding("JSONRPC") == "JSONRPC"
        assert wire_binding("grpc") == "GRPC"
        assert wire_binding("http+json") == "HTTP+JSON"

    def test_sdk_transports_are_lower_case(self):
        assert wire_binding("SlimRPC") == "slimrpc"
        assert wire_binding("slimpatterns") == "slimpatterns"

    def test_aliases_are_resolved(self):
        assert wire_binding("slim") == "slimpatterns"
        assert wire_binding("NATS") == "natspatterns"
        assert wire_binding("slim-extended") == "slimpatterns"


class TestReaders:
    def test_preferred_transport_is_first_interface(self):
        card = _card(("slimpatterns", "slim://a"), ("JSONRPC", "http://h"))
        assert preferred_transport(card) == "slimpatterns"

    def test_preferred_transport_none_without_interfaces(self):
        assert preferred_transport(_card()) is None

    def test_get_card_url_is_first_interface_url(self):
        card = _card(("slimpatterns", "slim://a"), ("JSONRPC", "http://h"))
        assert get_card_url(card) == "slim://a"

    def test_get_card_url_none_without_interfaces(self):
        assert get_card_url(_card()) is None

    def test_find_interface_matches_aliases_and_case(self):
        card = _card(("slimpatterns", "slim://a"), ("JSONRPC", "http://h"))
        found = find_interface(card, "SLIM")
        assert found is not None
        assert found.url == "slim://a"
        assert find_interface(card, "jsonrpc").url == "http://h"  # type: ignore[union-attr]

    def test_find_interface_returns_none_when_missing(self):
        assert find_interface(_card(("JSONRPC", "http://h")), "slimrpc") is None


class TestCloneCard:
    def test_clone_is_independent(self):
        original = _card(("JSONRPC", "http://h"))
        duplicate = clone_card(original)
        duplicate.supported_interfaces[0].url = "http://changed"
        duplicate.name = "other"

        assert original.supported_interfaces[0].url == "http://h"
        assert original.name == "agent"


class TestAddInterface:
    def test_appends_missing_interface(self):
        card = _card(("JSONRPC", "http://h"))

        iface = add_interface(card, "slimpatterns", "slim://a")

        assert iface.url == "slim://a"
        assert _pairs(card) == [("JSONRPC", "http://h"), ("slimpatterns", "slim://a")]

    def test_prefer_prepends_new_interface(self):
        card = _card(("JSONRPC", "http://h"))

        add_interface(card, "slimpatterns", "slim://a", prefer=True)

        assert _pairs(card) == [("slimpatterns", "slim://a"), ("JSONRPC", "http://h")]
        assert preferred_transport(card) == "slimpatterns"

    def test_existing_interface_is_left_untouched_by_default(self):
        card = _card(("JSONRPC", "http://h"), ("slimpatterns", "slim://orig"))

        iface = add_interface(card, "slim", "slim://new")

        assert iface.url == "slim://orig"
        assert _pairs(card) == [
            ("JSONRPC", "http://h"),
            ("slimpatterns", "slim://orig"),
        ]

    def test_replace_overwrites_existing_url(self):
        card = _card(("slimpatterns", "slim://orig"))

        add_interface(card, "slimpatterns", "slim://new", replace=True)

        assert _pairs(card) == [("slimpatterns", "slim://new")]

    def test_prefer_moves_existing_interface_to_front(self):
        card = _card(
            ("JSONRPC", "http://h"),
            ("natspatterns", "nats://n"),
            ("slimpatterns", "slim://a"),
        )

        add_interface(card, "slimpatterns", "slim://a", prefer=True)

        assert _pairs(card) == [
            ("slimpatterns", "slim://a"),
            ("JSONRPC", "http://h"),
            ("natspatterns", "nats://n"),
        ]

    def test_prefer_keeps_other_interfaces_intact_after_reorder(self):
        """Regression: protobuf wrappers go stale on reorder; values must survive."""
        card = AgentCard(
            name="agent",
            supported_interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC",
                    url="http://h",
                    protocol_version="1.0",
                    tenant="t1",
                ),
                AgentInterface(protocol_binding="slimpatterns", url="slim://a"),
            ],
        )

        add_interface(card, "slimpatterns", "slim://a", prefer=True)

        jsonrpc = card.supported_interfaces[1]
        assert (jsonrpc.protocol_binding, jsonrpc.url) == ("JSONRPC", "http://h")
        assert (jsonrpc.protocol_version, jsonrpc.tenant) == ("1.0", "t1")

    def test_new_interface_uses_wire_casing(self):
        card = _card()

        add_interface(card, "jsonrpc", "http://h")
        add_interface(card, "slim", "slim://a")

        assert _pairs(card) == [("JSONRPC", "http://h"), ("slimpatterns", "slim://a")]

    def test_protocol_version_is_set_on_new_interface(self):
        card = _card()

        iface = add_interface(card, "jsonrpc", "http://h", protocol_version="1.0")

        assert iface.protocol_version == "1.0"
