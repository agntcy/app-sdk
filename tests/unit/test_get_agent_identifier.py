# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for agntcy_app_sdk.semantic.a2a.utils.get_agent_identifier."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface

from agntcy_app_sdk.semantic.a2a.utils import get_agent_identifier


def _make_card(
    *,
    url: str = "http://localhost:9999",
    preferred_transport: str | None = None,
    additional_interfaces: list[AgentInterface] | None = None,
) -> AgentCard:
    return AgentCard(
        name="Test Agent",
        description="A test agent",
        url=url,
        version="1.0.0",
        skills=[],
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(),
        preferred_transport=preferred_transport,
        additional_interfaces=additional_interfaces,
    )


# ---------------------------------------------------------------------------
# With explicit interface_type
# ---------------------------------------------------------------------------


class TestWithInterfaceType:
    def test_match_slim_topic_only(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_topic_only(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="natspatterns", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_slim_explicit_endpoint(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(
                    transport="slimpatterns",
                    url="slim://localhost:46357/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_match_nats_explicit_endpoint(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(
                    transport="natspatterns",
                    url="nats://localhost:4222/my_topic",
                ),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") == "my_topic"

    def test_match_with_slashes(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(
                    transport="slimpatterns",
                    url="slim://default/default/agent",
                ),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "default/default/agent"

    def test_no_match_returns_none(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "natspatterns") is None

    def test_no_interfaces_returns_none(self):
        card = _make_card()
        assert get_agent_identifier(card, "slimpatterns") is None

    def test_case_insensitive_match(self):
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="SlimPatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "SLIMPATTERNS") == "my_topic"

    def test_http_interface_returns_none(self):
        """HTTP URLs don't have patterns-scheme topics to extract."""
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="jsonrpc", url="http://localhost:9999"),
            ],
        )
        assert get_agent_identifier(card, "jsonrpc") is None


# ---------------------------------------------------------------------------
# Without interface_type (auto-detect via preferred_transport)
# ---------------------------------------------------------------------------


class TestWithoutInterfaceType:
    def test_preferred_matches_interface(self):
        card = _make_card(
            preferred_transport="slimpatterns",
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_preferred_falls_back_to_card_url(self):
        card = _make_card(
            url="slim://Weather_Agent_1.0.0",
            preferred_transport="slimpatterns",
        )
        # urlparse lowercases the hostname portion
        assert get_agent_identifier(card) == "weather_agent_1.0.0"

    def test_no_preferred_returns_none(self):
        card = _make_card()
        assert get_agent_identifier(card) is None

    def test_preferred_no_match_falls_back_to_url(self):
        """preferred_transport doesn't match any interface but card.url is patterns."""
        card = _make_card(
            url="nats://fallback_topic",
            preferred_transport="natspatterns",
        )
        assert get_agent_identifier(card) == "fallback_topic"

    def test_preferred_no_match_http_url_returns_none(self):
        """preferred_transport set but card.url is HTTP — no topic extractable."""
        card = _make_card(
            url="http://localhost:9999",
            preferred_transport="slimpatterns",
        )
        assert get_agent_identifier(card) is None

    def test_multiple_interfaces_picks_matching(self):
        card = _make_card(
            preferred_transport="natspatterns",
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://slim_topic"),
                AgentInterface(transport="natspatterns", url="nats://nats_topic"),
            ],
        )
        assert get_agent_identifier(card) == "nats_topic"


# ---------------------------------------------------------------------------
# Transport alias resolution
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """Verify that transport aliases (e.g. 'slim' → 'slimpatterns') match."""

    def test_alias_in_interface_type_param(self):
        """Caller passes alias 'slim'; card uses canonical 'slimpatterns'."""
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slim") == "my_topic"

    def test_alias_in_card_interface(self):
        """Card uses alias 'slim'; caller passes canonical 'slimpatterns'."""
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "slimpatterns") == "my_topic"

    def test_both_aliases(self):
        """Both card and caller use the alias 'nats'."""
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="nats", url="nats://my_topic"),
            ],
        )
        assert get_agent_identifier(card, "nats") == "my_topic"

    def test_slim_extended_alias(self):
        """'slim-extended' alias resolves to 'slimpatterns'."""
        card = _make_card(
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://topic"),
            ],
        )
        assert get_agent_identifier(card, "slim-extended") == "topic"

    def test_preferred_transport_alias(self):
        """preferred_transport set to alias 'slim'; interface is canonical."""
        card = _make_card(
            preferred_transport="slim",
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"

    def test_preferred_transport_canonical_interface_alias(self):
        """preferred_transport canonical; interface has alias."""
        card = _make_card(
            preferred_transport="slimpatterns",
            additional_interfaces=[
                AgentInterface(transport="slim", url="slim://my_topic"),
            ],
        )
        assert get_agent_identifier(card) == "my_topic"
