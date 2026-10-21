# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier."""

import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentInterface

from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import parse_interface_url
from agntcy_app_sdk.semantic.a2a.utils import get_agent_identifier


def _make_card(
    *,
    interfaces: list[AgentInterface] | None = None,
) -> AgentCard:
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        version="1.0.0",
        skills=[],
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(),
        supported_interfaces=interfaces,
    )


# ---------------------------------------------------------------------------
# With explicit interface_type
# ---------------------------------------------------------------------------


class TestWithInterfaceType:
    def test_match_slim_topic_only(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_topic_only(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="natspatterns", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_slim_three_part_name_keeps_case(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns", url="slim://Org/NS/Agent"
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "Org/NS/Agent"

    def test_match_slim_explicit_endpoint(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://localhost:46357/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_explicit_endpoint(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="natspatterns",
                    url="nats://localhost:4222/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_with_slashes(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://default/default/agent",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "default/default/agent"

    def test_no_match_returns_none(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") is None

    def test_no_interfaces_returns_none(self) -> None:
        card = _make_card()
        assert get_agent_identifier(card, "slimpatterns") is None

    def test_case_insensitive_match(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="SlimPatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "SLIMPATTERNS") == "my_topic"

    def test_http_interface_returns_none(self) -> None:
        """HTTP URLs don't have patterns-scheme topics to extract."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="jsonrpc", url="http://localhost:9999"),
            ],
        )
        assert get_agent_identifier(card, "jsonrpc") is None


# ---------------------------------------------------------------------------
# Without interface_type (auto-detect via the first / preferred interface)
# ---------------------------------------------------------------------------


class TestWithoutInterfaceType:
    def test_first_interface_is_used(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_single_patterns_url(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://Weather_Agent_1.0.0",
                ),
            ],
        )
        assert get_agent_identifier(card) == "Weather_Agent_1.0.0"

    def test_no_interfaces_returns_none(self) -> None:
        card = _make_card()
        assert get_agent_identifier(card) is None

    def test_http_first_skipped_for_later_patterns(self) -> None:
        """An HTTP interface has no topic, so the next interface in card
        order supplies it."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9999"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card) == "topic"

    def test_only_http_returns_none(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9999"),
            ],
        )
        assert get_agent_identifier(card) is None

    def test_bare_slimrpc_identity_first(self) -> None:
        """The SLIM-RPC server handler declares a bare org/ns/name first."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimrpc", url="org/ns/agent"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card) == "org/ns/agent"

    def test_http_first_then_bare_slimrpc(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9999"),
                AgentInterface(protocol_binding="slimrpc", url="org/ns/agent"),
            ],
        )
        assert get_agent_identifier(card) == "org/ns/agent"

    def test_bare_name_on_patterns_binding_is_not_an_identity(self) -> None:
        """Only slimrpc declares bare names; a patterns URL needs a scheme."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="my_topic"),
            ],
        )
        assert get_agent_identifier(card) is None

    def test_multiple_interfaces_picks_first(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="natspatterns", url="nats://nats_topic"
                ),
                AgentInterface(
                    protocol_binding="slimpatterns", url="slim://slim_topic"
                ),
            ],
        )
        assert get_agent_identifier(card) == "nats_topic"

    def test_explicit_type_overrides_order(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="natspatterns", url="nats://nats_topic"
                ),
                AgentInterface(
                    protocol_binding="slimpatterns", url="slim://slim_topic"
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "slim_topic"

    def test_explicit_slimrpc_bare_identity(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
                AgentInterface(protocol_binding="slimrpc", url="org/ns/agent"),
            ],
        )
        assert get_agent_identifier(card, "slimrpc") == "org/ns/agent"

    def test_explicit_slimrpc_slim_url(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimrpc",
                    url="slim://localhost:46357/org/ns/agent",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimrpc") == "org/ns/agent"


# ---------------------------------------------------------------------------
# Transport alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """Verify that transport aliases (e.g. 'slim' → 'slimpatterns') match."""

    def test_alias_in_interface_type_param(self) -> None:
        """Caller passes alias 'slim'; card uses canonical 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slim") == "my_topic"

    def test_alias_in_card_interface(self) -> None:
        """Card uses alias 'slim'; caller passes canonical 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_both_aliases(self) -> None:
        """Both card and caller use the alias 'nats'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="nats", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "nats") == "my_topic"

    def test_slim_extended_alias(self) -> None:
        """'slim-extended' alias resolves to 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card, "slim-extended") == "topic"

    def test_first_interface_alias(self) -> None:
        """The first interface uses alias 'slim'; auto-detect still resolves it."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_auto_detect_canonical_with_other_alias_present(self) -> None:
        """Auto-detect picks the first interface; later aliases don't interfere."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://first"),
                AgentInterface(protocol_binding="slim", url="slim://second"),
            ],
        )
        assert get_agent_identifier(card) == "first"


# ---------------------------------------------------------------------------
# SLIM interfaces with http(s):// URLs (accepted by card_bootstrap)
# ---------------------------------------------------------------------------

_HTTPS_SLIM_URL = "https://slim.example.com:46357/org/ns/agent"


class TestSlimHttpUrls:
    def test_https_slimpatterns_explicit_endpoint(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url=_HTTPS_SLIM_URL),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "org/ns/agent"
        assert get_agent_identifier(card) == "org/ns/agent"

    def test_https_slimrpc_explicit_endpoint(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimrpc", url=_HTTPS_SLIM_URL),
            ],
        )
        assert get_agent_identifier(card, "slimrpc") == "org/ns/agent"

    def test_http_slim_topic_only(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slim", url="http://org/ns/agent"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "org/ns/agent"

    def test_https_slim_first_not_skipped_for_nats(self) -> None:
        """The https SLIM interface is usable, so another bus's topic must not win."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url=_HTTPS_SLIM_URL),
                AgentInterface(protocol_binding="natspatterns", url="nats://other"),
            ],
        )
        assert get_agent_identifier(card) == "org/ns/agent"

    def test_https_on_nats_binding_is_not_a_topic(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="natspatterns", url="https://h:4222/topic"
                ),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") is None

    def test_jsonrpc_with_path_still_skipped(self) -> None:
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="JSONRPC", url="http://localhost:9999/org/ns/a"
                ),
            ],
        )
        assert get_agent_identifier(card) is None

    @pytest.mark.parametrize(
        "url",
        [
            "slim://localhost:46357/org/ns/agent/",
            "slim://org/ns/agent/",
            "https://localhost:46357/org/ns/agent/",
        ],
    )
    def test_trailing_slash_not_part_of_identifier(self, url: str) -> None:
        card = _make_card(
            interfaces=[AgentInterface(protocol_binding="slimpatterns", url=url)],
        )
        assert get_agent_identifier(card) == "org/ns/agent"

    @pytest.mark.parametrize(
        ("binding", "key"), [("slimpatterns", "topic"), ("slimrpc", "identity")]
    )
    @pytest.mark.parametrize(
        "url",
        [
            "slim://localhost:46357/org/ns/agent",
            "slim://org/ns/agent",
            "https://localhost:46357/org/ns/agent/",
            "HTTPS://Localhost:46357/org/ns/agent",
            "https://org/ns/agent",
        ],
    )
    def test_matches_card_bootstrap_name(
        self, binding: str, key: str, url: str
    ) -> None:
        """The identifier is the name the server actually serves on."""
        iface = AgentInterface(protocol_binding=binding, url=url)
        card = _make_card(interfaces=[iface])
        assert get_agent_identifier(card, binding) == parse_interface_url(iface)[key]
