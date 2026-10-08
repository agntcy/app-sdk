# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface

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
    def test_match_slim_topic_only(self):
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_topic_only(self):
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="natspatterns", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_slim_explicit_endpoint(self):
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://localhost:46357/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_explicit_endpoint(self):
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="natspatterns",
                    url="nats://localhost:4222/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_with_slashes(self):
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://default/default/agent",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "default/default/agent"

    def test_no_match_returns_none(self):
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") is None

    def test_no_interfaces_returns_none(self):
        card = _make_card()
        assert get_agent_identifier(card, "slimpatterns") is None

    def test_case_insensitive_match(self):
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="SlimPatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "SLIMPATTERNS") == "my_topic"

    def test_http_interface_returns_none(self):
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
    def test_first_interface_is_used(self):
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_single_patterns_url(self):
        card = _make_card(
            interfaces=[
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://Weather_Agent_1.0.0",
                ),
            ],
        )
        # urlparse lowercases the hostname portion
        assert get_agent_identifier(card) == "weather_agent_1.0.0"

    def test_no_interfaces_returns_none(self):
        card = _make_card()
        assert get_agent_identifier(card) is None

    def test_http_first_returns_none(self):
        """List order is the server's preference: an HTTP interface listed
        first means the preferred transport has no topic to extract, even if
        a patterns interface follows."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9999"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card) is None

    def test_multiple_interfaces_picks_first(self):
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

    def test_explicit_type_overrides_order(self):
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


# ---------------------------------------------------------------------------
# Transport alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """Verify that transport aliases (e.g. 'slim' → 'slimpatterns') match."""

    def test_alias_in_interface_type_param(self):
        """Caller passes alias 'slim'; card uses canonical 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slim") == "my_topic"

    def test_alias_in_card_interface(self):
        """Card uses alias 'slim'; caller passes canonical 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_both_aliases(self):
        """Both card and caller use the alias 'nats'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="nats", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "nats") == "my_topic"

    def test_slim_extended_alias(self):
        """'slim-extended' alias resolves to 'slimpatterns'."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card, "slim-extended") == "topic"

    def test_first_interface_alias(self):
        """The first interface uses alias 'slim'; auto-detect still resolves it."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_auto_detect_canonical_with_other_alias_present(self):
        """Auto-detect picks the first interface; later aliases don't interfere."""
        card = _make_card(
            interfaces=[
                AgentInterface(protocol_binding="slimpatterns", url="slim://first"),
                AgentInterface(protocol_binding="slim", url="slim://second"),
            ],
        )
        assert get_agent_identifier(card) == "first"
