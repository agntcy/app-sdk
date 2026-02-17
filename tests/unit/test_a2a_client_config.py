# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AgentCard-centric A2A client stack:
ClientConfig, PatternsClientTransport, A2AExperimentalClient, A2AClientFactory.
"""

import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from a2a.client.client import Client
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
# Transport config dataclass tests
# ---------------------------------------------------------------------------


class TestTransportConfigs:
    def test_slim_transport_config_requires_fields(self):
        """SlimTransportConfig should require endpoint and name."""
        from agntcy_app_sdk.semantic.a2a.client.config import SlimTransportConfig

        cfg = SlimTransportConfig(endpoint="http://localhost:46357", name="a/b/c")
        assert cfg.endpoint == "http://localhost:46357"
        assert cfg.name == "a/b/c"

    def test_slim_transport_config_missing_name_raises(self):
        """SlimTransportConfig without name should raise TypeError."""
        from agntcy_app_sdk.semantic.a2a.client.config import SlimTransportConfig

        with pytest.raises(TypeError):
            SlimTransportConfig(endpoint="http://localhost:46357")  # type: ignore[call-arg]

    def test_nats_transport_config_requires_endpoint(self):
        """NatsTransportConfig should require endpoint."""
        from agntcy_app_sdk.semantic.a2a.client.config import NatsTransportConfig

        cfg = NatsTransportConfig(endpoint="nats://localhost:4222")
        assert cfg.endpoint == "nats://localhost:4222"

    def test_nats_transport_config_missing_endpoint_raises(self):
        """NatsTransportConfig without endpoint should raise TypeError."""
        from agntcy_app_sdk.semantic.a2a.client.config import NatsTransportConfig

        with pytest.raises(TypeError):
            NatsTransportConfig()  # type: ignore[call-arg]

    def test_slim_rpc_config_requires_all_fields(self):
        """SlimRpcConfig should require namespace, group, name."""
        from agntcy_app_sdk.semantic.a2a.client.config import SlimRpcConfig

        cfg = SlimRpcConfig(namespace="agntcy", group="demo", name="client")
        assert cfg.namespace == "agntcy"
        assert cfg.group == "demo"
        assert cfg.name == "client"


# ---------------------------------------------------------------------------
# ClientConfig tests
# ---------------------------------------------------------------------------


class TestClientConfig:
    def test_extends_upstream_config(self):
        """ClientConfig should extend A2AClientConfig with new fields."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig()
        assert config.slim_config is None
        assert config.slim_transport is None
        assert config.nats_config is None
        assert config.nats_transport is None
        assert config.slimrpc_config is None
        assert config.slimrpc_channel_factory is None
        # Upstream fields should be present
        assert config.streaming is True

    def test_post_init_default_jsonrpc(self):
        """Empty config should auto-derive supported_transports with JSONRPC."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig()
        assert config.supported_transports == ["JSONRPC"]

    def test_post_init_slim_config(self):
        """Setting slim_config should auto-add slimpatterns."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            SlimTransportConfig,
        )

        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        assert "JSONRPC" in config.supported_transports
        assert "slimpatterns" in config.supported_transports

    def test_post_init_slim_transport(self):
        """Setting slim_transport should auto-add slimpatterns."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig(slim_transport=_make_mock_transport())
        assert "slimpatterns" in config.supported_transports

    def test_post_init_nats_config(self):
        """Setting nats_config should auto-add natspatterns."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            NatsTransportConfig,
        )

        config = ClientConfig(
            nats_config=NatsTransportConfig(endpoint="nats://localhost:4222"),
        )
        assert "natspatterns" in config.supported_transports

    def test_post_init_nats_transport(self):
        """Setting nats_transport should auto-add natspatterns."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig(nats_transport=_make_mock_transport("NATS"))
        assert "natspatterns" in config.supported_transports

    def test_post_init_slimrpc_channel_factory(self):
        """Setting slimrpc_channel_factory should auto-add slimrpc."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig(slimrpc_channel_factory=MagicMock())
        assert "slimrpc" in config.supported_transports

    def test_post_init_slimrpc_config(self):
        """Setting slimrpc_config should auto-add slimrpc."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            SlimRpcConfig,
        )

        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="agntcy", group="demo", name="client"
            ),
        )
        assert "slimrpc" in config.supported_transports

    def test_post_init_multiple_transports(self):
        """Multiple configs should all appear in supported_transports."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            NatsTransportConfig,
            SlimTransportConfig,
        )

        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
            nats_config=NatsTransportConfig(endpoint="nats://localhost:4222"),
            slimrpc_channel_factory=MagicMock(),
        )
        assert "JSONRPC" in config.supported_transports
        assert "slimpatterns" in config.supported_transports
        assert "natspatterns" in config.supported_transports
        assert "slimrpc" in config.supported_transports

    def test_explicit_supported_transports_not_overridden(self):
        """If user explicitly sets supported_transports, __post_init__ should not override."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

        config = ClientConfig(
            supported_transports=["custom_transport"],
            slim_transport=_make_mock_transport(),
        )
        # User's explicit list should be preserved
        assert config.supported_transports == ["custom_transport"]


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
    def test_create_slim_eager(self):
        """create() should use slim_transport from config for slim labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(
            slim_transport=mock_transport,
        )
        card = _make_agent_card(preferred_transport="slimpatterns")

        transport = PatternsClientTransport.create(card, "slim://topic_1", config, [])
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"
        assert transport._agent_card is card

    def test_create_nats_eager(self):
        """create() should use nats_transport from config for nats labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport("NATS")
        config = ClientConfig(
            nats_transport=mock_transport,
        )
        card = _make_agent_card(preferred_transport="natspatterns")

        transport = PatternsClientTransport.create(card, "nats://topic_1", config, [])
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"

    def test_create_no_transport_raises(self):
        """create() should raise if no pre-built transport is on config."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            SlimTransportConfig,
        )
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        # Only deferred config, no eager transport — sync create() can't handle it
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        card = _make_agent_card(preferred_transport="slimpatterns")

        with pytest.raises(ValueError, match="No pre-built transport"):
            PatternsClientTransport.create(card, "slim://topic_1", config, [])

    def test_create_unknown_transport_raises(self):
        """create() should raise for unknown transport labels."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        config = ClientConfig(supported_transports=["unknown"])
        card = _make_agent_card(preferred_transport="unknown")

        with pytest.raises(ValueError, match="No pre-built transport"):
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

        await pct.send_message(params)
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

        card_result = await pct.get_card()
        assert card_result is card

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
# A2AExperimentalClient tests
# ---------------------------------------------------------------------------


class TestA2AExperimentalClient:
    def test_properties(self):
        """Experimental client should expose agent_card, transport, topic properties."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        mock_client = MagicMock()
        mock_client._consumers = []
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        experimental = A2AExperimentalClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        assert experimental.agent_card is card
        assert experimental.upstream_client is mock_client
        assert experimental.transport is mock_transport
        assert experimental.topic == "test_topic"

    def test_is_client_subclass(self):
        """A2AExperimentalClient should be a subclass of Client."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        mock_client = MagicMock()
        mock_client._consumers = []
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        experimental = A2AExperimentalClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        assert isinstance(experimental, Client)

    def test_experimental_methods_available(self):
        """Experimental client should have broadcast and groupchat methods."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        mock_client = MagicMock()
        mock_client._consumers = []
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        experimental = A2AExperimentalClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        assert hasattr(experimental, "broadcast_message")
        assert hasattr(experimental, "broadcast_message_streaming")
        assert hasattr(experimental, "start_groupchat")
        assert hasattr(experimental, "start_streaming_groupchat")
        assert callable(experimental.broadcast_message)
        assert callable(experimental.start_groupchat)

    @pytest.mark.asyncio
    async def test_get_card(self):
        """get_card should return the cached agent card."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        mock_client = MagicMock()
        mock_client._consumers = []
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        experimental = A2AExperimentalClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )

        result = await experimental.get_card()
        assert result is card


# ---------------------------------------------------------------------------
# A2AClientFactory tests
# ---------------------------------------------------------------------------


class TestA2AClientFactory:
    def test_constructor_default_config(self):
        """Factory with no config should use defaults."""
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        factory = A2AClientFactory()
        assert factory._config.supported_transports == ["JSONRPC"]

    def test_constructor_with_config(self):
        """Factory should accept and store a ClientConfig."""
        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            SlimTransportConfig,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        factory = A2AClientFactory(config)
        assert factory._config is config
        assert "slimpatterns" in factory._config.supported_transports

    # -- Negotiation tests --------------------------------------------------

    def test_negotiate_server_preference(self):
        """Default negotiation should prefer server's transport."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slim_transport=_make_mock_transport(),
        )
        factory = A2AClientFactory(config)

        # Card prefers slimpatterns, client supports both
        card = _make_agent_card(
            preferred_transport="slimpatterns",
            url="slim://my_agent",
        )
        label, url = factory._negotiate(card)
        assert label == "slimpatterns"
        assert url == "slim://my_agent"

    def test_negotiate_fallback_to_jsonrpc(self):
        """If server offers unknown transport + JSONRPC, should fall back."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig()  # only JSONRPC
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://my_agent",
            additional_interfaces=[
                AgentInterface(transport="JSONRPC", url="http://localhost:8080"),
            ],
        )
        label, url = factory._negotiate(card)
        assert label == "JSONRPC"
        assert url == "http://localhost:8080"

    def test_negotiate_no_match_raises(self):
        """Negotiation should raise if no compatible transports."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(supported_transports=["custom_only"])
        factory = A2AClientFactory(config)

        card = _make_agent_card(preferred_transport="grpc", url="grpc://agent")
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    def test_negotiate_client_preference(self):
        """With use_client_preference, client's order should win."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slim_transport=_make_mock_transport(),
            nats_transport=_make_mock_transport("NATS"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        # Server prefers natspatterns, but client's list has JSONRPC first
        card = _make_agent_card(
            preferred_transport="natspatterns",
            url="nats://my_agent",
            additional_interfaces=[
                AgentInterface(transport="JSONRPC", url="http://localhost:8080"),
            ],
        )
        label, url = factory._negotiate(card)
        # Client's supported_transports is ["JSONRPC", "slimpatterns", "natspatterns"]
        # JSONRPC appears first and server offers it
        assert label == "JSONRPC"
        assert url == "http://localhost:8080"

    # -- create() async path tests ------------------------------------------

    @pytest.mark.asyncio
    async def test_create_with_eager_slim_transport(self):
        """create() with eager slim_transport should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(slim_transport=mock_transport)
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            preferred_transport="slimpatterns",
            url="slim://my_agent",
        )
        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert isinstance(result, Client)
        assert result.agent_card is card
        assert result.transport is mock_transport
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_with_eager_nats_transport(self):
        """create() with eager nats_transport should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        mock_transport = _make_mock_transport("NATS")
        config = ClientConfig(nats_transport=mock_transport)
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            preferred_transport="natspatterns",
            url="nats://my_agent",
        )
        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert isinstance(result, Client)
        assert result.agent_card is card
        assert result.transport is mock_transport
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_jsonrpc_returns_upstream_client(self):
        """create() for JSONRPC should return upstream Client, not A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig()
        factory = A2AClientFactory(config)

        card = _make_agent_card()  # defaults to JSONRPC
        result = await factory.create(card)

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)

    @pytest.mark.asyncio
    async def test_create_deferred_slim_missing_config_raises(self):
        """create() with slimpatterns but no config or transport should raise."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        # Force slimpatterns in supported_transports but provide no config
        config = ClientConfig(supported_transports=["slimpatterns", "JSONRPC"])
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            preferred_transport="slimpatterns",
            url="slim://my_agent",
        )
        with pytest.raises(ValueError, match="neither slim_transport nor slim_config"):
            await factory.create(card)

    # -- connect() classmethod test -----------------------------------------

    @pytest.mark.asyncio
    async def test_connect_with_card(self):
        """connect() with an AgentCard should skip HTTP resolution."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        card = _make_agent_card()  # JSONRPC default
        config = ClientConfig()

        result = await A2AClientFactory.connect(card, config=config)
        assert isinstance(result, Client)


# ---------------------------------------------------------------------------
# Multi-transport negotiation tests
# ---------------------------------------------------------------------------


class TestMultiTransportNegotiation:
    """Tests for a ClientConfig with slimrpc, slimpatterns, and natspatterns
    all configured simultaneously, verifying negotiation against various
    server agent cards.

    The factory is built once with all three transports (plus the implicit
    JSONRPC fallback).  Each test constructs an agent card that a real server
    would advertise and asserts the negotiation picks the correct transport.
    """

    # -- Fixture: factory with all three transports -------------------------

    @staticmethod
    def _make_multi_transport_factory():
        """Build an A2AClientFactory whose ClientConfig supports all transports."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            # slimrpc (eager channel factory)
            slimrpc_channel_factory=MagicMock(),
            # slimpatterns (eager transport)
            slim_transport=_make_mock_transport("SLIM"),
            # natspatterns (eager transport)
            nats_transport=_make_mock_transport("NATS"),
        )
        return A2AClientFactory(config), config

    # -- Auto-derived supported_transports ----------------------------------

    def test_supported_transports_contains_all(self):
        """ClientConfig with all three should auto-derive all four transports."""
        _factory, config = self._make_multi_transport_factory()
        assert "JSONRPC" in config.supported_transports
        assert "slimrpc" in config.supported_transports
        assert "slimpatterns" in config.supported_transports
        assert "natspatterns" in config.supported_transports
        assert len(config.supported_transports) == 4

    # -- Server prefers slimrpc ---------------------------------------------

    def test_server_prefers_slimrpc(self):
        """Card with preferred_transport=slimrpc should negotiate to slimrpc."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slimrpc",
            url="default/default/Hello_World_Agent_1.0.0",
        )
        label, url = factory._negotiate(card)
        assert label == "slimrpc"
        assert url == "default/default/Hello_World_Agent_1.0.0"

    # -- Server prefers slimpatterns ----------------------------------------

    def test_server_prefers_slimpatterns(self):
        """Card with preferred_transport=slimpatterns should negotiate to slimpatterns."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slimpatterns",
            url="slim://my_agent_topic",
        )
        label, url = factory._negotiate(card)
        assert label == "slimpatterns"
        assert url == "slim://my_agent_topic"

    # -- Server prefers natspatterns ----------------------------------------

    def test_server_prefers_natspatterns(self):
        """Card with preferred_transport=natspatterns should negotiate to natspatterns."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="natspatterns",
            url="nats://my_agent_topic",
        )
        label, url = factory._negotiate(card)
        assert label == "natspatterns"
        assert url == "nats://my_agent_topic"

    # -- Server prefers JSONRPC (explicit) ----------------------------------

    def test_server_prefers_jsonrpc(self):
        """Card with preferred_transport=JSONRPC should negotiate to JSONRPC."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="JSONRPC",
            url="http://localhost:9999",
        )
        label, url = factory._negotiate(card)
        assert label == "JSONRPC"
        assert url == "http://localhost:9999"

    # -- Server prefers JSONRPC (default / None) ----------------------------

    def test_server_default_transport_is_jsonrpc(self):
        """Card with no preferred_transport should default to JSONRPC."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(url="http://localhost:9999")
        label, url = factory._negotiate(card)
        assert label == "JSONRPC"
        assert url == "http://localhost:9999"

    # -- Server prefers unknown, fallback via additional_interfaces ---------

    def test_server_unknown_preferred_falls_back_to_additional(self):
        """Server prefers unsupported transport; client finds match in additional_interfaces."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://agent",
            additional_interfaces=[
                AgentInterface(transport="natspatterns", url="nats://agent_topic"),
                AgentInterface(transport="JSONRPC", url="http://localhost:9999"),
            ],
        )
        label, url = factory._negotiate(card)
        # Server's preferred "grpc" not supported → first match in server_set
        # iteration: grpc (skip), natspatterns (match!)
        assert label == "natspatterns"
        assert url == "nats://agent_topic"

    # -- Server offers multiple via additional_interfaces -------------------

    def test_server_preferred_plus_additional(self):
        """Server's preferred_transport wins even when additional_interfaces are present."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slimrpc",
            url="default/default/agent",
            additional_interfaces=[
                AgentInterface(transport="slimpatterns", url="slim://agent_topic"),
                AgentInterface(transport="natspatterns", url="nats://agent_topic"),
                AgentInterface(transport="JSONRPC", url="http://localhost:9999"),
            ],
        )
        label, url = factory._negotiate(card)
        assert label == "slimrpc"
        assert url == "default/default/agent"

    # -- Client preference mode overrides server ----------------------------

    def test_client_preference_overrides_server(self):
        """With use_client_preference=True, client's transport order wins."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slimrpc_channel_factory=MagicMock(),
            slim_transport=_make_mock_transport("SLIM"),
            nats_transport=_make_mock_transport("NATS"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        # Client's auto-derived order: ["JSONRPC", "slimpatterns", "natspatterns", "slimrpc"]
        # Server prefers slimrpc and also offers JSONRPC
        card = _make_agent_card(
            preferred_transport="slimrpc",
            url="default/default/agent",
            additional_interfaces=[
                AgentInterface(transport="JSONRPC", url="http://localhost:9999"),
            ],
        )
        label, url = factory._negotiate(card)
        # JSONRPC appears first in client's list and server offers it
        assert label == "JSONRPC"
        assert url == "http://localhost:9999"

    # -- No match at all raises ValueError ---------------------------------

    def test_no_match_raises(self):
        """Server only offers transports the client doesn't support → ValueError."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://agent",
            additional_interfaces=[
                AgentInterface(transport="websocket", url="ws://agent"),
            ],
        )
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    # -- create() dispatches to correct path --------------------------------

    @pytest.mark.asyncio
    async def test_create_dispatches_slimrpc(self):
        """create() with slimrpc card should return upstream Client (sync path)."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slimrpc",
            url="default/default/agent",
        )
        result = await factory.create(card)
        # slimrpc goes through the upstream sync path → upstream Client
        assert isinstance(result, Client)

    @pytest.mark.asyncio
    async def test_create_dispatches_slimpatterns(self):
        """create() with slimpatterns card should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slimpatterns",
            url="slim://my_agent",
        )
        result = await factory.create(card)
        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_natspatterns(self):
        """create() with natspatterns card should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="natspatterns",
            url="nats://my_agent",
        )
        result = await factory.create(card)
        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_jsonrpc_from_additional(self):
        """When server prefers unknown transport but offers JSONRPC in
        additional_interfaces, create() should fall through to JSONRPC."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://agent",
            additional_interfaces=[
                AgentInterface(transport="JSONRPC", url="http://localhost:9999"),
            ],
        )
        result = await factory.create(card)
        assert isinstance(result, Client)
