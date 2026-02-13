# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AgentCard-centric A2A client stack:
ClientConfig, PatternsClientTransport, A2AEnhancedClient, A2AClientFactory.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from a2a.types import (
    AgentCard,
    AgentInterface,
    MessageSendParams,
)

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_card(
    name: str = "test-agent",
    url: str = "http://localhost:8080",
    preferred_transport: str | None = None,
    additional_interfaces: list[AgentInterface] | None = None,
) -> AgentCard:
    """Create a minimal AgentCard for testing."""
    return AgentCard(
        name=name,
        url=url,
        version="1.0",
        skills=[],
        capabilities={},
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        description="Test agent",
        preferred_transport=preferred_transport,
        additional_interfaces=additional_interfaces,
    )


def _make_mock_transport(transport_type: str = "SLIM") -> MagicMock:
    """Create a mock BaseTransport."""
    transport = MagicMock()
    transport.type.return_value = transport_type
    transport.setup = AsyncMock()
    transport.close = AsyncMock()
    transport.request = AsyncMock()
    transport.gather = AsyncMock()
    transport.gather_stream = AsyncMock()
    transport.start_conversation = AsyncMock()
    transport.start_streaming_conversation = AsyncMock()
    return transport


# ---------------------------------------------------------------------------
# ClientConfig tests
# ---------------------------------------------------------------------------


class TestClientConfig:
    def test_extends_upstream_config(self):
        """ClientConfig should extend A2AClientConfig with extra fields."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig()
        assert config.slimrpc_channel_factory is None
        assert config.slim_patterns_transport_factory is None
        assert config.nats_transport_factory is None
        # Upstream fields should be present
        assert config.streaming is True
        assert config.supported_transports == []

    def test_from_card_preferred_transport(self):
        """from_card should include preferred_transport in supported_transports."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        card = _make_agent_card(preferred_transport="slimpatterns")
        config = ClientConfig.from_card(card)
        assert "slimpatterns" in config.supported_transports
        assert "JSONRPC" in config.supported_transports

    def test_from_card_additional_interfaces(self):
        """from_card should include additional_interfaces transports."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        card = _make_agent_card(
            preferred_transport="slimpatterns",
            additional_interfaces=[
                AgentInterface(transport="natspatterns", url="nats://localhost:4222"),
                AgentInterface(transport="slimrpc", url="slim://localhost:46357"),
            ],
        )
        config = ClientConfig.from_card(card)
        assert "slimpatterns" in config.supported_transports
        assert "natspatterns" in config.supported_transports
        assert "slimrpc" in config.supported_transports
        assert "JSONRPC" in config.supported_transports

    def test_from_card_no_preferred_transport(self):
        """from_card with no preferred_transport should still include JSONRPC."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        card = _make_agent_card()
        config = ClientConfig.from_card(card)
        assert "JSONRPC" in config.supported_transports

    def test_from_card_kwargs_forwarded(self):
        """from_card should forward kwargs to ClientConfig constructor."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        card = _make_agent_card(preferred_transport="slimpatterns")
        config = ClientConfig.from_card(card, streaming=False)
        assert config.streaming is False

    def test_from_card_no_duplicate_jsonrpc(self):
        """If preferred_transport is already JSONRPC, don't add it twice."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        card = _make_agent_card(preferred_transport="JSONRPC")
        config = ClientConfig.from_card(card)
        assert config.supported_transports.count("JSONRPC") == 1

    def test_factory_callables_settable(self):
        """Factory callables can be set after construction."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        factory = MagicMock()
        config = ClientConfig(slim_patterns_transport_factory=factory)
        assert config.slim_patterns_transport_factory is factory


# ---------------------------------------------------------------------------
# _parse_topic_from_url tests
# ---------------------------------------------------------------------------


class TestParseTopicFromUrl:
    def test_slim_scheme(self):
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("slim://my_topic") == "my_topic"

    def test_nats_scheme(self):
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("nats://my_topic") == "my_topic"

    def test_plain_topic(self):
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("my_topic") == "my_topic"

    def test_http_url_passthrough(self):
        """HTTP URLs should pass through unchanged (not a patterns scheme)."""
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("http://localhost:9999") == "http://localhost:9999"

    def test_topic_with_slashes(self):
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert (
            _parse_topic_from_url("slim://default/default/agent")
            == "default/default/agent"
        )


# ---------------------------------------------------------------------------
# PatternsClientTransport tests
# ---------------------------------------------------------------------------


class TestPatternsClientTransport:
    def test_create_slim(self):
        """create() should use slim_patterns_transport_factory for slim labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(
            slim_patterns_transport_factory=lambda: mock_transport,
            supported_transports=["slimpatterns"],
        )
        card = _make_agent_card(preferred_transport="slimpatterns")

        transport = PatternsClientTransport.create(card, "slim://topic_1", config, [])
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"
        assert transport._agent_card is card

    def test_create_nats(self):
        """create() should use nats_transport_factory for nats labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport("NATS")
        config = ClientConfig(
            nats_transport_factory=lambda: mock_transport,
            supported_transports=["natspatterns"],
        )
        card = _make_agent_card(preferred_transport="natspatterns")

        transport = PatternsClientTransport.create(card, "nats://topic_1", config, [])
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"

    def test_create_missing_factory_raises(self):
        """create() should raise if factory callable is None."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        config = ClientConfig(supported_transports=["slimpatterns"])
        card = _make_agent_card(preferred_transport="slimpatterns")

        with pytest.raises(ValueError, match="slim_patterns_transport_factory"):
            PatternsClientTransport.create(card, "topic_1", config, [])

    def test_create_unknown_transport_raises(self):
        """create() should raise for unknown transport labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        config = ClientConfig(supported_transports=["unknown"])
        card = _make_agent_card(preferred_transport="unknown")

        with pytest.raises(ValueError, match="cannot handle transport label"):
            PatternsClientTransport.create(card, "topic_1", config, [])

    @pytest.mark.asyncio
    async def test_send_message(self):
        """send_message should call transport.request and parse JSON response."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport()
        card = _make_agent_card()

        response_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "result": {
                "kind": "message",
                "messageId": str(uuid4()),
                "role": "agent",
                "parts": [{"kind": "text", "text": "Hello"}],
            },
        }
        mock_response = MagicMock()
        mock_response.payload = json.dumps(response_payload).encode("utf-8")
        mock_response.status_code = 200
        mock_transport.request.return_value = mock_response

        pct = PatternsClientTransport(mock_transport, card, "test_topic")

        # Create minimal MessageSendParams
        from a2a.types import Message as A2AMessage, Part, TextPart

        params = MessageSendParams(
            message=A2AMessage(
                messageId=str(uuid4()),
                role="user",
                parts=[Part(root=TextPart(kind="text", text="Hi"))],
            )
        )

        result = await pct.send_message(params)
        assert mock_transport.request.called

    @pytest.mark.asyncio
    async def test_get_card(self):
        """get_card should return the cached agent card."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport()
        card = _make_agent_card()
        pct = PatternsClientTransport(mock_transport, card, "test_topic")

        result = await pct.get_card()
        assert result is card

    @pytest.mark.asyncio
    async def test_close(self):
        """close should delegate to transport.close()."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport()
        card = _make_agent_card()
        pct = PatternsClientTransport(mock_transport, card, "test_topic")

        await pct.close()
        mock_transport.close.assert_called_once()


# ---------------------------------------------------------------------------
# A2AEnhancedClient tests
# ---------------------------------------------------------------------------


class TestA2AEnhancedClient:
    def test_properties(self):
        """Enhanced client should expose agent_card, transport, topic properties."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )

        mock_client = MagicMock()
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        enhanced = A2AEnhancedClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        assert enhanced.agent_card is card
        assert enhanced.upstream_client is mock_client
        assert enhanced.transport is mock_transport
        assert enhanced.topic == "test_topic"

    def test_experimental_methods_wired(self):
        """With transport + topic, experimental methods should be available."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )

        mock_client = MagicMock()
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        enhanced = A2AEnhancedClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        assert len(enhanced._experimental) == 4
        assert "broadcast_message" in enhanced._experimental
        assert "broadcast_message_streaming" in enhanced._experimental
        assert "start_groupchat" in enhanced._experimental
        assert "start_streaming_groupchat" in enhanced._experimental

    def test_no_experimental_without_transport(self):
        """Without transport, experimental methods should not be wired."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )

        mock_client = MagicMock()
        card = _make_agent_card()

        enhanced = A2AEnhancedClient(
            client=mock_client,
            agent_card=card,
        )

        assert len(enhanced._experimental) == 0

    @pytest.mark.asyncio
    async def test_broadcast_without_transport_raises(self):
        """broadcast_message without transport should raise RuntimeError."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )

        mock_client = MagicMock()
        card = _make_agent_card()

        enhanced = A2AEnhancedClient(client=mock_client, agent_card=card)

        with pytest.raises(RuntimeError, match="requires a transport"):
            await enhanced.broadcast_message(MagicMock(), recipients=["a"])

    @pytest.mark.asyncio
    async def test_groupchat_without_transport_raises(self):
        """start_groupchat without transport should raise RuntimeError."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )

        mock_client = MagicMock()
        card = _make_agent_card()

        enhanced = A2AEnhancedClient(client=mock_client, agent_card=card)

        with pytest.raises(RuntimeError, match="requires a transport"):
            await enhanced.start_groupchat(
                MagicMock(), group_channel="ch", participants=["a"]
            )


# ---------------------------------------------------------------------------
# A2AClientFactory tests
# ---------------------------------------------------------------------------


class TestA2AClientFactory:
    def test_protocol_type(self):
        """Factory should report 'A2A' as protocol type."""
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        factory = A2AClientFactory()
        assert factory.protocol_type() == "A2A"

    @pytest.mark.asyncio
    async def test_create_client_no_url_no_topic_raises(self):
        """create_client with neither url nor topic should raise ValueError."""
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        factory = A2AClientFactory()
        with pytest.raises(ValueError, match="Either url or topic"):
            await factory.create_client()

    @pytest.mark.asyncio
    async def test_create_client_with_transport(self):
        """create_client with transport should return A2AEnhancedClient."""
        from agntcy_app_sdk.semantic.a2a.client.enhanced_client import (
            A2AEnhancedClient,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        factory = A2AClientFactory()
        mock_transport = _make_mock_transport("SLIM")
        card = _make_agent_card(preferred_transport="slimpatterns")

        # Mock the card resolution
        with patch.object(
            factory,
            "_resolve_agent_card",
            new_callable=AsyncMock,
            return_value=card,
        ):
            result = await factory.create_client(
                topic="test_topic",
                transport=mock_transport,
            )

        assert isinstance(result, A2AEnhancedClient)
        assert result.agent_card is card
        assert result.transport is mock_transport
        mock_transport.setup.assert_called_once()
