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
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
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

    def test_slim_endpoint_with_port(self):
        """slim://host:port/topic should extract just the topic."""
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("slim://localhost:46357/my_topic") == "my_topic"

    def test_nats_endpoint_with_port(self):
        """nats://host:port/topic should extract just the topic."""
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert _parse_topic_from_url("nats://localhost:4222/my_topic") == "my_topic"

    def test_slim_endpoint_with_port_and_slashes(self):
        """slim://host:port/ns/group/name should extract the full path as topic."""
        from agntcy_app_sdk.semantic.a2a.client.transports import _parse_topic_from_url

        assert (
            _parse_topic_from_url("slim://localhost:46357/default/default/agent")
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


# =========================================================================
# Transport alias resolution in negotiation
# =========================================================================


class TestTransportAliasNegotiation:
    """Verify that transport aliases (e.g. "slim" -> "slimpatterns",
    "nats" -> "natspatterns") are resolved during client-side negotiation
    and dispatch so cards using alias names still produce valid clients.
    """

    @staticmethod
    def _make_multi_transport_factory():
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slimrpc_channel_factory=MagicMock(),
            slim_transport=_make_mock_transport("SLIM"),
            nats_transport=_make_mock_transport("NATS"),
        )
        return A2AClientFactory(config), config

    # -- negotiate() resolves aliases in preferred_transport ----------------

    def test_negotiate_slim_alias_preferred(self):
        """Card with preferred_transport='slim' should negotiate successfully."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slim",
            url="slim://my_topic",
        )
        label, url = factory._negotiate(card)
        assert label == "slim"
        assert url == "slim://my_topic"

    def test_negotiate_nats_alias_preferred(self):
        """Card with preferred_transport='nats' should negotiate successfully."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="nats",
            url="nats://my_topic",
        )
        label, url = factory._negotiate(card)
        assert label == "nats"
        assert url == "nats://my_topic"

    def test_negotiate_slim_extended_alias_preferred(self):
        """Card with preferred_transport='slim-extended' should negotiate."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slim-extended",
            url="slim://my_topic",
        )
        label, url = factory._negotiate(card)
        assert label == "slim-extended"
        assert url == "slim://my_topic"

    # -- negotiate() resolves aliases in additional_interfaces ---------------

    def test_negotiate_slim_alias_in_additional_interfaces(self):
        """Card with transport='slim' in additional_interfaces matches client's 'slimpatterns'."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://agent",
            additional_interfaces=[
                AgentInterface(transport="slim", url="slim://my_topic"),
            ],
        )
        label, url = factory._negotiate(card)
        assert label == "slim"
        assert url == "slim://my_topic"

    def test_negotiate_nats_alias_in_additional_interfaces(self):
        """Card with transport='nats' in additional_interfaces matches client's 'natspatterns'."""
        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="grpc",
            url="grpc://agent",
            additional_interfaces=[
                AgentInterface(transport="nats", url="nats://my_topic"),
            ],
        )
        label, url = factory._negotiate(card)
        assert label == "nats"
        assert url == "nats://my_topic"

    # -- create() dispatches correctly for aliased labels -------------------

    @pytest.mark.asyncio
    async def test_create_slim_alias_dispatches_to_slimpatterns(self):
        """create() with preferred_transport='slim' should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="slim",
            url="slim://my_agent",
        )
        result = await factory.create(card)
        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_nats_alias_dispatches_to_natspatterns(self):
        """create() with preferred_transport='nats' should return A2AExperimentalClient."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        factory, _config = self._make_multi_transport_factory()
        card = _make_agent_card(
            preferred_transport="nats",
            url="nats://my_agent",
        )
        result = await factory.create(card)
        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    # -- client_preference mode also resolves aliases -----------------------

    def test_client_preference_resolves_aliases(self):
        """With use_client_preference, aliased server transports still match."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        config = ClientConfig(
            slim_transport=_make_mock_transport("SLIM"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            preferred_transport="slim",
            url="slim://my_topic",
        )
        label, url = factory._negotiate(card)
        # Client supports "slimpatterns"; server offers "slim" (alias).
        # Alias resolution should make them match.
        assert label == "slim"
        assert url == "slim://my_topic"


# ---------------------------------------------------------------------------
# Tests for _build_slimrpc_if_needed() — trailing-slash connection isolation
# ---------------------------------------------------------------------------


class TestBuildSlimrpcIfNeeded:
    """Verify that ``_build_slimrpc_if_needed()`` opens a dedicated SLIM
    connection via the trailing-slash endpoint trick, matching the
    server-side pattern in ``A2ASRPCServerHandler``.
    """

    @pytest.mark.asyncio
    async def test_slimrpc_uses_trailing_slash_endpoint(self):
        """_build_slimrpc_if_needed should connect using endpoint + '/'."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from agntcy_app_sdk.semantic.a2a.client.config import (
            ClientConfig,
            SlimRpcConfig,
        )
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        mock_service = MagicMock()
        mock_service.connect_async = AsyncMock(return_value=42)
        mock_app = MagicMock()
        mock_service.create_app_with_secret = MagicMock(return_value=mock_app)

        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="lungo",
                group="agents",
                name="my_agent",
                slim_url="http://localhost:46357",
                secret="test-secret-32-chars-minimum-here",
            ),
        )
        factory = A2AClientFactory(config)

        with (
            patch(
                "agntcy_app_sdk.transport.slim.common.get_or_create_slim_instance",
                new_callable=AsyncMock,
                return_value=(mock_service, mock_app, 1),
            ) as mock_get_or_create,
            patch("slim_bindings.Name", MagicMock()) as mock_name,
            patch(
                "slim_bindings.new_insecure_client_config",
                MagicMock(return_value="rpc_config"),
            ) as mock_new_client_config,
            patch(
                "slima2a.client_transport.slimrpc_channel_factory",
                return_value=MagicMock(),
            ),
        ):
            # Wire connect_async on the service returned by get_or_create
            mock_service.connect_async = AsyncMock(return_value=99)
            # Wire the rpc app returned by create_app_with_secret
            mock_rpc_app = MagicMock()
            mock_rpc_app.subscribe_async = AsyncMock()
            mock_service.create_app_with_secret = MagicMock(return_value=mock_rpc_app)

            await factory._build_slimrpc_if_needed()

            # Should have called get_or_create_slim_instance first
            mock_get_or_create.assert_called_once()

            # Should have called new_insecure_client_config with trailing slash
            mock_new_client_config.assert_called_once_with("http://localhost:46357/")

            # Should have opened a second connection
            mock_service.connect_async.assert_called_once_with("rpc_config")

            # Should have created a separate app with "-rpc" suffix
            mock_name.assert_any_call("lungo", "agents", "my_agent-rpc")
            mock_service.create_app_with_secret.assert_called_once()

            # Should have subscribed the rpc app on the dedicated connection
            mock_rpc_app.subscribe_async.assert_called_once()

            # Channel factory should be set
            assert config.slimrpc_channel_factory is not None

    @pytest.mark.asyncio
    async def test_slimrpc_noop_when_eager_factory_set(self):
        """_build_slimrpc_if_needed should be a no-op if channel factory already set."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        eager_factory = MagicMock()
        config = ClientConfig(slimrpc_channel_factory=eager_factory)
        factory = A2AClientFactory(config)

        await factory._build_slimrpc_if_needed()

        # Should not have changed the factory
        assert config.slimrpc_channel_factory is eager_factory


# ---------------------------------------------------------------------------
# Interceptor test helper
# ---------------------------------------------------------------------------


class _RecordingInterceptor(ClientCallInterceptor):
    """Test interceptor that records calls and optionally modifies payloads.

    Subclasses the real ``ClientCallInterceptor`` ABC so that tests verify
    the actual interface contract rather than relying on duck-typing.
    """

    def __init__(self, modify_key=None, modify_value=None):
        self.calls: list[tuple] = []
        self._modify_key = modify_key
        self._modify_value = modify_value

    async def intercept(
        self,
        method_name: str,
        request_payload: dict,
        http_kwargs: dict,
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict, dict]:
        self.calls.append((method_name, dict(request_payload), dict(http_kwargs)))
        if self._modify_key:
            request_payload[self._modify_key] = self._modify_value
        return request_payload, http_kwargs


def _make_json_rpc_response(result: dict | None = None) -> MagicMock:
    """Create a mock transport response with a JSON-RPC payload."""
    resp = MagicMock()
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "result": result
        or {
            "kind": "message",
            "messageId": str(uuid4()),
            "role": "agent",
            "parts": [{"kind": "text", "text": "Hello"}],
        },
    }
    resp.payload = json.dumps(payload).encode("utf-8")
    resp.status_code = 200
    resp.type = "A2AResponse"
    return resp


# ---------------------------------------------------------------------------
# PatternsClientTransport interceptor tests
# ---------------------------------------------------------------------------


class TestPatternsClientTransportInterceptors:
    @pytest.mark.asyncio
    async def test_send_message_calls_interceptor(self):
        """send_message should invoke the interceptor with method_name='message/send'."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response()
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])

        from a2a.types import Message as A2AMessage, Part, TextPart

        params = MessageSendParams(
            message=A2AMessage(
                messageId=str(uuid4()),
                role="user",
                parts=[Part(root=TextPart(kind="text", text="Hi"))],
            )
        )
        await pct.send_message(params)

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"

    @pytest.mark.asyncio
    async def test_send_message_uses_modified_payload(self):
        """Interceptor modifications should reach the underlying transport."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        interceptor = _RecordingInterceptor(
            modify_key="x-custom", modify_value="injected"
        )
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response()
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])

        from a2a.types import Message as A2AMessage, Part, TextPart

        params = MessageSendParams(
            message=A2AMessage(
                messageId=str(uuid4()),
                role="user",
                parts=[Part(root=TextPart(kind="text", text="Hi"))],
            )
        )
        await pct.send_message(params)

        # Verify the interceptor was invoked
        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"
        # Verify the transport received the modified payload by inspecting
        # the Message object passed to transport.request().  The second
        # positional arg is the transport Message built from the intercepted
        # payload.
        call_args = mock_transport.request.call_args
        transport_msg = call_args[0][1]  # second positional arg
        payload_data = transport_msg.payload
        import json as _json

        # payload may be str or bytes depending on message_translator
        if isinstance(payload_data, bytes):
            payload_data = payload_data.decode("utf-8")
        sent_payload = _json.loads(payload_data)
        assert sent_payload.get("x-custom") == "injected"

    @pytest.mark.asyncio
    async def test_get_task_calls_interceptor(self):
        """get_task should invoke the interceptor with method_name='tasks/get'."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        from a2a.types import TaskQueryParams

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response(
            result={
                "kind": "task",
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "completed"},
            }
        )
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])
        await pct.get_task(TaskQueryParams(id="task-1"))

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "tasks/get"

    @pytest.mark.asyncio
    async def test_cancel_task_calls_interceptor(self):
        """cancel_task should invoke the interceptor with method_name='tasks/cancel'."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        from a2a.types import TaskIdParams

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response(
            result={
                "kind": "task",
                "id": "task-1",
                "contextId": "ctx-1",
                "status": {"state": "canceled"},
            }
        )
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])
        await pct.cancel_task(TaskIdParams(id="task-1"))

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "tasks/cancel"

    @pytest.mark.asyncio
    async def test_set_task_callback_calls_interceptor(self):
        """set_task_callback should invoke the interceptor with the correct method_name."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        from a2a.types import TaskPushNotificationConfig

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response(
            result={
                "taskId": "task-1",
                "pushNotificationConfig": {"url": "http://example.com/callback"},
            }
        )
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])
        await pct.set_task_callback(
            TaskPushNotificationConfig(
                taskId="task-1",
                pushNotificationConfig={"url": "http://example.com/callback"},
            )
        )

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "tasks/pushNotificationConfig/set"

    @pytest.mark.asyncio
    async def test_get_task_callback_calls_interceptor(self):
        """get_task_callback should invoke the interceptor with the correct method_name."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        from a2a.types import GetTaskPushNotificationConfigParams

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response(
            result={
                "taskId": "task-1",
                "pushNotificationConfig": {"url": "http://example.com/callback"},
            }
        )
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])
        await pct.get_task_callback(GetTaskPushNotificationConfigParams(id="task-1"))

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "tasks/pushNotificationConfig/get"

    @pytest.mark.asyncio
    async def test_send_message_streaming_calls_interceptor(self):
        """send_message_streaming should invoke the interceptor with 'message/stream'."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport()
        card = _make_agent_card()

        # Mock request_stream as an async generator
        stream_response = _make_json_rpc_response()

        async def mock_request_stream(topic, msg):
            yield stream_response

        mock_transport.request_stream = mock_request_stream

        pct = PatternsClientTransport(mock_transport, card, "topic", [interceptor])

        from a2a.types import Message as A2AMessage, Part, TextPart

        params = MessageSendParams(
            message=A2AMessage(
                messageId=str(uuid4()),
                role="user",
                parts=[Part(root=TextPart(kind="text", text="Hi"))],
            )
        )

        # Consume the async generator
        results = []
        async for event in pct.send_message_streaming(params):
            results.append(event)

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/stream"

    @pytest.mark.asyncio
    async def test_interceptor_chaining_order(self):
        """Multiple interceptors should be applied in order, composing modifications."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        first = _RecordingInterceptor(modify_key="step", modify_value="first")
        second = _RecordingInterceptor(modify_key="step", modify_value="second")
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response()
        card = _make_agent_card()

        pct = PatternsClientTransport(mock_transport, card, "topic", [first, second])

        from a2a.types import Message as A2AMessage, Part, TextPart

        params = MessageSendParams(
            message=A2AMessage(
                messageId=str(uuid4()),
                role="user",
                parts=[Part(root=TextPart(kind="text", text="Hi"))],
            )
        )
        await pct.send_message(params)

        # Both interceptors called
        assert len(first.calls) == 1
        assert len(second.calls) == 1
        # Second interceptor sees the modification from the first
        assert second.calls[0][1].get("step") == "first"

    @pytest.mark.asyncio
    async def test_no_interceptors_passthrough(self):
        """With no interceptors, send_message should still work normally."""
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _make_json_rpc_response()
        card = _make_agent_card()

        # No interceptors — empty list
        pct = PatternsClientTransport(mock_transport, card, "topic", [])

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

    def test_create_forwards_interceptors(self):
        """PatternsClientTransport.create() should store interceptors."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.transports import (
            PatternsClientTransport,
        )

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(slim_transport=mock_transport)
        card = _make_agent_card(preferred_transport="slimpatterns")

        pct = PatternsClientTransport.create(
            card, "slim://topic_1", config, [interceptor]
        )
        assert pct._interceptors == [interceptor]


# ---------------------------------------------------------------------------
# A2AExperimentalClient interceptor tests
# ---------------------------------------------------------------------------


class TestA2AExperimentalClientInterceptors:
    def _make_experimental_client(self, interceptors=None):
        """Helper to construct an A2AExperimentalClient with mocks."""
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        mock_client = MagicMock()
        mock_client._consumers = []
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        return (
            A2AExperimentalClient(
                client=mock_client,
                agent_card=card,
                transport=mock_transport,
                topic="test_topic",
                interceptors=interceptors,
            ),
            mock_transport,
            card,
        )

    @pytest.mark.asyncio
    async def test_broadcast_message_calls_interceptor(self):
        """broadcast_message should apply the interceptor to the payload."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = self._make_experimental_client([interceptor])

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )

        # Mock gather_stream to return an empty async iterator
        async def empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover

        mock_transport.gather_stream = empty_stream

        await client.broadcast_message(request, recipients=["agent-1"])

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_calls_interceptor(self):
        """broadcast_message_streaming should apply the interceptor to the payload."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendStreamingMessageRequest,
            TextPart,
        )

        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = self._make_experimental_client([interceptor])

        request = SendStreamingMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )

        # Mock gather_stream to return an empty async iterator
        async def empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover

        mock_transport.gather_stream = empty_stream

        results = []
        async for event in client.broadcast_message_streaming(
            request, recipients=["agent-1"]
        ):
            results.append(event)

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"

    @pytest.mark.asyncio
    async def test_start_groupchat_calls_interceptor(self):
        """start_groupchat should apply the interceptor to the init message."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = self._make_experimental_client([interceptor])

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )

        # Mock start_conversation to return empty list
        mock_transport.start_conversation = AsyncMock(return_value=[])

        await client.start_groupchat(
            request, group_channel="group", participants=["a", "b"]
        )

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"

    @pytest.mark.asyncio
    async def test_start_streaming_groupchat_calls_interceptor(self):
        """start_streaming_groupchat should apply the interceptor to the init message."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = self._make_experimental_client([interceptor])

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )

        # Mock start_streaming_conversation to return empty async iterator
        async def empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover

        mock_transport.start_streaming_conversation = empty_stream

        results = []
        async for event in client.start_streaming_groupchat(
            request, group_channel="group", participants=["a", "b"]
        ):
            results.append(event)

        assert len(interceptor.calls) == 1
        assert interceptor.calls[0][0] == "message/send"


# ---------------------------------------------------------------------------
# Factory-level interceptor integration tests
# ---------------------------------------------------------------------------


class TestFactoryInterceptorIntegration:
    """Verify interceptors are wired end-to-end through
    ``A2AClientFactory.create()`` for the patterns transport path.
    """

    @pytest.mark.asyncio
    async def test_factory_create_patterns_interceptor_invoked(self):
        """Interceptor passed to factory.create() should fire on send_message
        through the full BaseClient -> PatternsClientTransport chain."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        interceptor = _RecordingInterceptor()
        mock_transport = _make_mock_transport("SLIM")
        mock_transport.request.return_value = _make_json_rpc_response()

        config = ClientConfig(slim_transport=mock_transport)
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            url="slim://test_topic",
            preferred_transport="slimpatterns",
        )

        client = await factory.create(card, interceptors=[interceptor])

        from a2a.types import Message as A2AMessage, Part, TextPart

        msg = A2AMessage(
            messageId=str(uuid4()),
            role="user",
            parts=[Part(root=TextPart(kind="text", text="Hello"))],
        )

        # send_message is an async iterator; consume it
        async for _event in client.send_message(msg):
            pass

        # The interceptor must have been called at least once via
        # PatternsClientTransport._send_rpc -> _apply_interceptors
        assert len(interceptor.calls) >= 1
        assert interceptor.calls[0][0] == "message/send"

    @pytest.mark.asyncio
    async def test_factory_create_patterns_interceptor_modifies_payload(self):
        """Payload modifications made by the interceptor should reach the
        underlying transport when going through the full factory path."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        interceptor = _RecordingInterceptor(
            modify_key="x-trace-id", modify_value="abc-123"
        )
        mock_transport = _make_mock_transport("SLIM")
        mock_transport.request.return_value = _make_json_rpc_response()

        config = ClientConfig(slim_transport=mock_transport)
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            url="slim://test_topic",
            preferred_transport="slimpatterns",
        )

        client = await factory.create(card, interceptors=[interceptor])

        from a2a.types import Message as A2AMessage, Part, TextPart

        msg = A2AMessage(
            messageId=str(uuid4()),
            role="user",
            parts=[Part(root=TextPart(kind="text", text="Hello"))],
        )

        async for _event in client.send_message(msg):
            pass

        # Verify the interceptor was called
        assert len(interceptor.calls) == 1

        # Verify the modified payload reached the underlying transport
        call_args = mock_transport.request.call_args
        transport_msg = call_args[0][1]
        payload_data = transport_msg.payload
        if isinstance(payload_data, bytes):
            payload_data = payload_data.decode("utf-8")
        sent_payload = json.loads(payload_data)
        assert sent_payload.get("x-trace-id") == "abc-123"

    @pytest.mark.asyncio
    async def test_factory_create_patterns_consumers_wired(self):
        """Consumers passed to factory.create() should be invoked on
        send_message responses through the full factory path."""
        from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
        from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory

        consumed_events: list = []

        async def recording_consumer(event, card):
            consumed_events.append((event, card))

        mock_transport = _make_mock_transport("SLIM")
        mock_transport.request.return_value = _make_json_rpc_response()

        config = ClientConfig(slim_transport=mock_transport)
        factory = A2AClientFactory(config)

        card = _make_agent_card(
            url="slim://test_topic",
            preferred_transport="slimpatterns",
        )

        client = await factory.create(
            card,
            consumers=[recording_consumer],
        )

        from a2a.types import Message as A2AMessage, Part, TextPart

        msg = A2AMessage(
            messageId=str(uuid4()),
            role="user",
            parts=[Part(root=TextPart(kind="text", text="Hello"))],
        )

        async for _event in client.send_message(msg):
            pass

        # The consumer should have been invoked by the inner BaseClient
        # during send_message processing
        assert len(consumed_events) >= 1


# ---------------------------------------------------------------------------
# Experimental-client consumer tests
# ---------------------------------------------------------------------------


class TestA2AExperimentalClientConsumers:
    """Verify that consumer callbacks fire for all experimental operations."""

    def _make_experimental_client_with_consumer(self):
        """Helper to build an ``A2AExperimentalClient`` with a recording consumer.

        The recording consumer is placed on the mock inner client's
        ``_consumers`` list so that ``super().__init__()`` copies it into
        the experimental client's own ``_consumers``.
        """
        from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
            A2AExperimentalClient,
        )

        consumed_events: list = []

        async def recording_consumer(event, card):
            consumed_events.append((event, card))

        mock_client = MagicMock()
        mock_client._consumers = [recording_consumer]
        mock_client._middleware = []
        card = _make_agent_card()
        mock_transport = _make_mock_transport()

        client = A2AExperimentalClient(
            client=mock_client,
            agent_card=card,
            transport=mock_transport,
            topic="test_topic",
        )
        return client, mock_transport, card, consumed_events

    # -- broadcast_message --------------------------------------------------

    @pytest.mark.asyncio
    async def test_broadcast_message_consumer_fires(self):
        """broadcast_message should invoke consumers for each response."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        task_result = {
            "kind": "task",
            "id": "task-1",
            "contextId": "ctx-1",
            "status": {"state": "completed"},
        }
        mock_resp = MagicMock()
        mock_resp.type = "A2AResponse"
        mock_resp.payload = json.dumps(
            {"jsonrpc": "2.0", "id": "1", "result": task_result}
        ).encode("utf-8")

        async def one_response(*args, **kwargs):
            yield mock_resp

        mock_transport.gather_stream = one_response

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        await client.broadcast_message(request, recipients=["agent-1"])

        assert len(consumed) == 1
        event, event_card = consumed[0]
        # Event should be (Task, None)
        assert isinstance(event, tuple)
        assert event[1] is None
        assert event_card == card

    @pytest.mark.asyncio
    async def test_broadcast_message_consumer_with_message_result(self):
        """broadcast_message consumer should fire for Message results."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        msg_result = {
            "kind": "message",
            "messageId": "msg-1",
            "role": "agent",
            "parts": [{"kind": "text", "text": "Hello back"}],
        }
        mock_resp = MagicMock()
        mock_resp.type = "A2AResponse"
        mock_resp.payload = json.dumps(
            {"jsonrpc": "2.0", "id": "1", "result": msg_result}
        ).encode("utf-8")

        async def one_response(*args, **kwargs):
            yield mock_resp

        mock_transport.gather_stream = one_response

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        await client.broadcast_message(request, recipients=["agent-1"])

        assert len(consumed) == 1
        event, event_card = consumed[0]
        # Event should be a Message, not a tuple
        assert isinstance(event, A2AMessage)
        assert event_card == card

    @pytest.mark.asyncio
    async def test_broadcast_message_consumer_skips_errors(self):
        """JSON-RPC error responses should not trigger consumers."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        mock_resp = MagicMock()
        mock_resp.type = "A2AResponse"
        mock_resp.payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "error": {"code": -32600, "message": "Invalid Request"},
            }
        ).encode("utf-8")

        async def one_response(*args, **kwargs):
            yield mock_resp

        mock_transport.gather_stream = one_response

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        await client.broadcast_message(request, recipients=["agent-1"])

        assert len(consumed) == 0

    @pytest.mark.asyncio
    async def test_broadcast_message_consumer_empty_stream(self):
        """Empty broadcast stream should not trigger consumers."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        async def empty_stream(*args, **kwargs):
            return
            yield  # pragma: no cover

        mock_transport.gather_stream = empty_stream

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        await client.broadcast_message(request, recipients=["agent-1"])

        assert len(consumed) == 0

    @pytest.mark.asyncio
    async def test_broadcast_message_consumer_multiple_responses(self):
        """N broadcast responses should produce N consumer invocations."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        def _make_resp(task_id):
            resp = MagicMock()
            resp.type = "A2AResponse"
            resp.payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "kind": "task",
                        "id": task_id,
                        "contextId": "ctx-1",
                        "status": {"state": "completed"},
                    },
                }
            ).encode("utf-8")
            return resp

        async def three_responses(*args, **kwargs):
            yield _make_resp("task-1")
            yield _make_resp("task-2")
            yield _make_resp("task-3")

        mock_transport.gather_stream = three_responses

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        await client.broadcast_message(request, recipients=["a1", "a2", "a3"])

        assert len(consumed) == 3

    # -- broadcast_message_streaming ----------------------------------------

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_consumer_fires(self):
        """broadcast_message_streaming should invoke consumers for each event."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendStreamingMessageRequest,
            Task,
            TaskStatusUpdateEvent,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        # Intermediate status-update
        intermediate_resp = MagicMock()
        intermediate_resp.type = "A2AStatusUpdate"
        intermediate_resp.status_code = 200
        intermediate_resp.payload = json.dumps(
            {
                "result": {
                    "kind": "status-update",
                    "taskId": "task-1",
                    "contextId": "ctx-1",
                    "status": {"state": "working"},
                    "final": False,
                }
            }
        ).encode("utf-8")

        # Final task response
        final_resp = MagicMock()
        final_resp.type = "A2AResponse"
        final_resp.status_code = 200
        final_resp.payload = json.dumps(
            {
                "result": {
                    "kind": "task",
                    "id": "task-1",
                    "contextId": "ctx-1",
                    "status": {"state": "completed"},
                }
            }
        ).encode("utf-8")

        async def mixed_stream(*args, **kwargs):
            yield intermediate_resp
            yield final_resp

        mock_transport.gather_stream = mixed_stream

        request = SendStreamingMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )

        results = []
        async for event in client.broadcast_message_streaming(
            request, recipients=["agent-1"]
        ):
            results.append(event)

        # Both intermediate and final events should trigger consumers
        assert len(consumed) == 2
        # First consumed event: (task_stub, TaskStatusUpdateEvent)
        first_event, first_card = consumed[0]
        assert isinstance(first_event, tuple)
        assert isinstance(first_event[1], TaskStatusUpdateEvent)
        assert first_card == card
        # Second consumed event: (Task, None)
        second_event, second_card = consumed[1]
        assert isinstance(second_event, tuple)
        assert isinstance(second_event[0], Task)
        assert second_event[1] is None

    # -- start_groupchat ----------------------------------------------------

    @pytest.mark.asyncio
    async def test_start_groupchat_consumer_fires(self):
        """start_groupchat should invoke consumers for each response."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        def _make_raw_msg(task_id):
            msg = MagicMock()
            msg.payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "kind": "task",
                        "id": task_id,
                        "contextId": "ctx-1",
                        "status": {"state": "completed"},
                    },
                }
            ).encode("utf-8")
            return msg

        mock_transport.start_conversation = AsyncMock(
            return_value=[_make_raw_msg("task-1"), _make_raw_msg("task-2")]
        )

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        responses = await client.start_groupchat(
            request, group_channel="grp", participants=["a", "b"]
        )

        assert len(responses) == 2
        assert len(consumed) == 2

    # -- start_streaming_groupchat ------------------------------------------

    @pytest.mark.asyncio
    async def test_start_streaming_groupchat_consumer_fires(self):
        """start_streaming_groupchat should invoke consumers for each response."""
        from a2a.types import (
            Message as A2AMessage,
            Part,
            SendMessageRequest,
            TextPart,
        )

        client, mock_transport, card, consumed = (
            self._make_experimental_client_with_consumer()
        )

        def _make_raw_msg(task_id):
            msg = MagicMock()
            msg.payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {
                        "kind": "task",
                        "id": task_id,
                        "contextId": "ctx-1",
                        "status": {"state": "completed"},
                    },
                }
            ).encode("utf-8")
            return msg

        async def streaming_conversation(*args, **kwargs):
            yield _make_raw_msg("task-1")
            yield _make_raw_msg("task-2")

        mock_transport.start_streaming_conversation = streaming_conversation

        request = SendMessageRequest(
            id="req-1",
            params=MessageSendParams(
                message=A2AMessage(
                    messageId=str(uuid4()),
                    role="user",
                    parts=[Part(root=TextPart(kind="text", text="Hi"))],
                )
            ),
        )
        results = []
        async for event in client.start_streaming_groupchat(
            request, group_channel="grp", participants=["a", "b"]
        ):
            results.append(event)

        assert len(results) == 2
        assert len(consumed) == 2
