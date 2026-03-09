# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for ``agntcy_app_sdk.semantic.a2a.server.card_bootstrap``."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import (
    CardBuilder,
    InterfaceTransport,
    ServeCardPlan,
    parse_interface_url,
)

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SKILL = AgentSkill(
    id="test",
    name="test skill",
    description="test",
    tags=["test"],
    examples=["hi"],
)


def _make_card(
    interfaces: list[AgentInterface] | None = None,
    name: str = "Test Agent",
) -> AgentCard:
    """Return a minimal AgentCard with the given additional_interfaces."""
    return AgentCard(
        name=name,
        description="A test agent",
        url="http://localhost:9999/",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[_SKILL],
        supportsAuthenticatedExtendedCard=False,
        additional_interfaces=interfaces,
    )


def _make_builder(
    interfaces: list[AgentInterface],
    session: MagicMock | None = None,
    factory: MagicMock | None = None,
) -> CardBuilder:
    """Create a CardBuilder with mock session and optional mock factory."""
    card = _make_card(interfaces=interfaces)
    handler = MagicMock()
    if session is None:
        session = MagicMock()
    builder = CardBuilder(session, card, handler)
    if factory is not None:
        builder.with_factory(factory)
    return builder


# =========================================================================
# parse_interface_url — slimrpc
# =========================================================================


class TestParseSlimRpcExplicitEndpoint:
    """slimrpc with explicit host:port in URL."""

    def test_basic(self):
        iface = AgentInterface(
            transport="slimrpc",
            url="slim://myhost:46357/org/ns/agent_name",
        )
        result = parse_interface_url(iface)
        assert result == {
            "endpoint": "http://myhost:46357",
            "identity": "org/ns/agent_name",
        }

    def test_custom_port(self):
        iface = AgentInterface(
            transport="slimrpc", url="slim://myhost:9999/org/ns/name"
        )
        result = parse_interface_url(iface)
        assert result["endpoint"] == "http://myhost:9999"
        assert result["identity"] == "org/ns/name"


class TestParseSlimRpcTopicOnly:
    """slimrpc with topic-only URL (identity encoded as path)."""

    def test_identity_only(self):
        """slim://org/ns/name -> identity, endpoint from env/default."""
        iface = AgentInterface(transport="slimrpc", url="slim://org/ns/agent_name")
        result = parse_interface_url(iface)
        assert result["identity"] == "org/ns/agent_name"
        # Default endpoint
        assert result["endpoint"] == "http://localhost:46357"

    def test_identity_only_with_env(self):
        """SLIM_ENDPOINT env var overrides the default."""
        iface = AgentInterface(transport="slimrpc", url="slim://org/ns/agent_name")
        with patch.dict(os.environ, {"SLIM_ENDPOINT": "http://custom:11111"}):
            result = parse_interface_url(iface)
        assert result["endpoint"] == "http://custom:11111"
        assert result["identity"] == "org/ns/agent_name"

    def test_missing_identity_raises(self):
        iface = AgentInterface(transport="slimrpc", url="slim://")
        with pytest.raises(ValueError, match="identity"):
            parse_interface_url(iface)


# =========================================================================
# parse_interface_url — slim / slimpatterns
# =========================================================================


class TestParseSlimExplicitEndpoint:
    """slim/slimpatterns with explicit host:port in URL."""

    def test_with_port(self):
        iface = AgentInterface(transport="slim", url="slim://myhost:46357/my_topic")
        result = parse_interface_url(iface)
        assert result == {
            "endpoint": "http://myhost:46357",
            "topic": "my_topic",
        }

    def test_slimpatterns_with_port(self):
        iface = AgentInterface(
            transport="slimpatterns", url="slim://host:12345/agent_topic"
        )
        result = parse_interface_url(iface)
        assert result["endpoint"] == "http://host:12345"
        assert result["topic"] == "agent_topic"

    def test_without_port_but_with_path(self):
        """slim://host/topic has a path, so treated as explicit."""
        iface = AgentInterface(transport="slim", url="slim://host/my_topic")
        result = parse_interface_url(iface)
        assert result["endpoint"] == "http://host:46357"
        assert result["topic"] == "my_topic"


class TestParseSlimTopicOnly:
    """slim/slimpatterns with topic-only URL (existing convention)."""

    def test_topic_only(self):
        """slim://my_topic -> topic, endpoint from default."""
        iface = AgentInterface(transport="slim", url="slim://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"
        assert result["endpoint"] == "http://localhost:46357"

    def test_topic_only_with_env(self):
        """SLIM_ENDPOINT env var overrides the default."""
        iface = AgentInterface(transport="slimpatterns", url="slim://my_topic")
        with patch.dict(os.environ, {"SLIM_ENDPOINT": "http://slim:9999"}):
            result = parse_interface_url(iface)
        assert result["endpoint"] == "http://slim:9999"
        assert result["topic"] == "my_topic"

    def test_topic_with_underscores(self):
        """Topic names like Hello_World_1.0.0 from create_agent_topic()."""
        iface = AgentInterface(
            transport="slimpatterns", url="slim://Hello_World_Agent_1.0.0"
        )
        result = parse_interface_url(iface)
        assert result["topic"] == "hello_world_agent_1.0.0"
        assert result["endpoint"] == "http://localhost:46357"

    def test_empty_topic_raises(self):
        iface = AgentInterface(transport="slim", url="slim://")
        with pytest.raises(ValueError, match="topic"):
            parse_interface_url(iface)

    def test_trailing_slash_only_is_explicit_empty(self):
        """slim://host:46357/ has explicit endpoint but no topic."""
        iface = AgentInterface(transport="slim", url="slim://host:46357/")
        with pytest.raises(ValueError, match="topic"):
            parse_interface_url(iface)


# =========================================================================
# parse_interface_url — nats / natspatterns
# =========================================================================


class TestParseNatsExplicitEndpoint:
    """nats/natspatterns with explicit host:port in URL."""

    def test_basic(self):
        iface = AgentInterface(transport="nats", url="nats://natshost:4222/my_topic")
        result = parse_interface_url(iface)
        assert result == {
            "endpoint": "nats://natshost:4222",
            "topic": "my_topic",
        }

    def test_natspatterns(self):
        iface = AgentInterface(
            transport="natspatterns", url="nats://host:5555/agent_topic"
        )
        result = parse_interface_url(iface)
        assert result["endpoint"] == "nats://host:5555"
        assert result["topic"] == "agent_topic"


class TestParseNatsTopicOnly:
    """nats/natspatterns with topic-only URL (existing convention)."""

    def test_topic_only(self):
        """nats://my_topic -> topic, endpoint from default."""
        iface = AgentInterface(transport="nats", url="nats://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"
        assert result["endpoint"] == "nats://localhost:4222"

    def test_topic_only_with_env(self):
        """NATS_ENDPOINT env var overrides the default."""
        iface = AgentInterface(transport="natspatterns", url="nats://agent_topic")
        with patch.dict(os.environ, {"NATS_ENDPOINT": "nats://nats:9999"}):
            result = parse_interface_url(iface)
        assert result["endpoint"] == "nats://nats:9999"
        assert result["topic"] == "agent_topic"

    def test_empty_topic_raises(self):
        iface = AgentInterface(transport="nats", url="nats://")
        with pytest.raises(ValueError, match="topic"):
            parse_interface_url(iface)


# =========================================================================
# parse_interface_url — jsonrpc / http
# =========================================================================


class TestParseHttp:
    def test_jsonrpc(self):
        iface = AgentInterface(transport="jsonrpc", url="http://0.0.0.0:9999")
        result = parse_interface_url(iface)
        assert result == {"host": "0.0.0.0", "port": 9999}

    def test_http(self):
        iface = AgentInterface(transport="http", url="http://localhost:8080")
        result = parse_interface_url(iface)
        assert result == {"host": "localhost", "port": 8080}

    def test_default_port(self):
        iface = AgentInterface(transport="http", url="http://localhost")
        result = parse_interface_url(iface)
        assert result["port"] == 9000

    def test_case_insensitive(self):
        iface = AgentInterface(transport="JSONRPC", url="http://0.0.0.0:9999")
        result = parse_interface_url(iface)
        assert result == {"host": "0.0.0.0", "port": 9999}


# =========================================================================
# parse_interface_url — unknown transport
# =========================================================================


class TestParseUnknown:
    def test_unknown_transport_raises(self):
        iface = AgentInterface(transport="grpc", url="grpc://host:50051")
        with pytest.raises(ValueError, match="Unknown transport type"):
            parse_interface_url(iface)


# =========================================================================
# CardBuilder — validation
# =========================================================================


class TestCardBuilderValidation:
    @pytest.mark.asyncio
    async def test_raises_on_empty_interfaces(self):
        builder = _make_builder(interfaces=[])

        with pytest.raises(ValueError, match="empty"):
            await builder.start()

    @pytest.mark.asyncio
    async def test_raises_on_none_interfaces(self):
        card = _make_card(interfaces=None)
        session = MagicMock()
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)

        with pytest.raises(ValueError, match="empty"):
            await builder.start()

    @pytest.mark.asyncio
    async def test_raises_on_missing_slim_shared_secret(self):
        """slimrpc interface declared but SLIM_SHARED_SECRET not set."""
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                )
            ]
        )

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLIM_SHARED_SECRET", None)
            with pytest.raises(ValueError, match="SLIM_SHARED_SECRET"):
                await builder.start()

    @pytest.mark.asyncio
    async def test_raises_on_missing_slim_shared_secret_for_slim_transport(self):
        """slim interface declared but SLIM_SHARED_SECRET not set."""
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slim",
                    url="slim://my_topic",
                )
            ]
        )

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLIM_SHARED_SECRET", None)
            with pytest.raises(ValueError, match="SLIM_SHARED_SECRET"):
                await builder.start()


# =========================================================================
# CardBuilder — dry-run
# =========================================================================


class TestCardBuilderDryRun:
    """dry_run() should return a plan without creating containers."""

    @pytest.mark.asyncio
    async def test_dry_run_slimrpc_topic_only(self):
        """slimrpc with topic-only URL (identity format)."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                )
            ],
            session=session,
        )

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        assert isinstance(plan, ServeCardPlan)
        assert len(plan.containers) == 1
        entry = plan.containers[0]
        assert entry["session_id"] == "slimrpc-0"
        assert entry["transport"] == "slimrpc"
        assert "org/ns/agent" in entry["detail"]
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_slimrpc_explicit_endpoint(self):
        """slimrpc with explicit host:port in URL."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://host:46357/org/ns/agent",
                )
            ],
            session=session,
        )

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        entry = plan.containers[0]
        assert "http://host:46357" in entry["detail"]
        assert "org/ns/agent" in entry["detail"]

    @pytest.mark.asyncio
    async def test_dry_run_slim_topic_only(self):
        """slim with topic-only URL (existing convention)."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slim",
                    url="slim://my_topic",
                )
            ],
            session=session,
        )

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        assert len(plan.containers) == 1
        assert plan.containers[0]["session_id"] == "slim-0"
        assert "my_topic" in plan.containers[0]["detail"]
        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_nats_topic_only(self):
        """nats with topic-only URL (existing convention)."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="nats",
                    url="nats://my_topic",
                )
            ],
            session=session,
        )

        plan = await builder.dry_run()

        assert len(plan.containers) == 1
        assert plan.containers[0]["session_id"] == "nats-0"
        assert "my_topic" in plan.containers[0]["detail"]

    @pytest.mark.asyncio
    async def test_dry_run_http(self):
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ],
            session=session,
        )

        plan = await builder.dry_run()

        assert len(plan.containers) == 1
        entry = plan.containers[0]
        assert entry["session_id"] == "http-0"
        assert "0.0.0.0" in entry["detail"]
        assert "9999" in entry["detail"]

    @pytest.mark.asyncio
    async def test_dry_run_multiple_transports(self):
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                ),
                AgentInterface(
                    transport="nats",
                    url="nats://topic1",
                ),
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:8080",
                ),
            ],
            session=session,
        )

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        assert len(plan.containers) == 3
        ids = [c["session_id"] for c in plan.containers]
        assert ids == ["slimrpc-0", "nats-1", "http-2"]

    @pytest.mark.asyncio
    async def test_dry_run_str_output(self):
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ],
            session=session,
        )

        plan = await builder.dry_run()

        plan_str = str(plan)
        assert "serve_card plan:" in plan_str
        assert "http-0" in plan_str


# =========================================================================
# CardBuilder — container building
# =========================================================================


class TestCardBuilderBuildsContainers:
    """Non-dry-run mode: verify the fluent builder calls per transport."""

    @pytest.mark.asyncio
    async def test_builds_slimrpc_container_explicit_endpoint(self):
        mock_builder = MagicMock()
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://host:46357/org/ns/agent",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            await builder.start()

        session.add.assert_called_once()
        config = session.add.call_args[0][0]

        from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASlimRpcServerConfig

        assert isinstance(config, A2ASlimRpcServerConfig)
        assert config.connection.identity == "org/ns/agent"
        assert config.connection.endpoint == "http://host:46357"
        assert config.connection.shared_secret == "secret" * 6

        mock_builder.with_session_id.assert_called_once_with("slimrpc-0")
        session.start_all_sessions.assert_awaited_once_with(keep_alive=False)

    @pytest.mark.asyncio
    async def test_builds_slimrpc_container_topic_only(self):
        """Topic-only format uses SLIM_ENDPOINT env or default."""
        mock_builder = MagicMock()
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(
            os.environ,
            {
                "SLIM_SHARED_SECRET": "secret" * 6,
                "SLIM_ENDPOINT": "http://custom:11111",
            },
        ):
            await builder.start()

        config = session.add.call_args[0][0]
        assert config.connection.identity == "org/ns/agent"
        assert config.connection.endpoint == "http://custom:11111"

    @pytest.mark.asyncio
    async def test_builds_nats_container_explicit_endpoint(self):
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        mock_transport = MagicMock()
        factory = MagicMock()
        factory.create_transport.return_value = mock_transport

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="nats",
                    url="nats://nhost:4222/my_topic",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        await builder.start()

        factory.create_transport.assert_called_once_with(
            "NATS", endpoint="nats://nhost:4222"
        )
        session.add.assert_called_once()
        mock_builder.with_transport.assert_called_once_with(mock_transport)
        mock_builder.with_topic.assert_called_once_with("my_topic")
        mock_builder.with_session_id.assert_called_once_with("nats-0")
        session.start_all_sessions.assert_awaited_once_with(keep_alive=False)

    @pytest.mark.asyncio
    async def test_builds_nats_container_topic_only(self):
        """Topic-only format: nats://my_topic uses NATS_ENDPOINT or default."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        mock_transport = MagicMock()
        factory = MagicMock()
        factory.create_transport.return_value = mock_transport

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="natspatterns",
                    url="nats://my_topic",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(os.environ, {"NATS_ENDPOINT": "nats://custom:9999"}):
            await builder.start()

        factory.create_transport.assert_called_once_with(
            "NATS", endpoint="nats://custom:9999"
        )
        mock_builder.with_topic.assert_called_once_with("my_topic")

    @pytest.mark.asyncio
    async def test_builds_http_container(self):
        mock_builder = MagicMock()
        mock_builder.with_host.return_value = mock_builder
        mock_builder.with_port.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        await builder.start()

        session.add.assert_called_once()
        mock_builder.with_host.assert_called_once_with("0.0.0.0")
        mock_builder.with_port.assert_called_once_with(9999)
        mock_builder.with_session_id.assert_called_once_with("http-0")
        session.start_all_sessions.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_builds_slim_container_topic_only(self):
        """Topic-only format: slim://my_topic."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        mock_transport = MagicMock()
        factory = MagicMock()
        factory.create_transport.return_value = mock_transport

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slim",
                    url="slim://my_topic",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(
            os.environ,
            {
                "SLIM_SHARED_SECRET": "secret" * 6,
                "SLIM_ENDPOINT": "http://slim:46357",
            },
        ):
            await builder.start()

        factory.create_transport.assert_called_once_with(
            "SLIM",
            endpoint="http://slim:46357",
            name="my_topic",
            shared_secret_identity="secret" * 6,
        )
        mock_builder.with_topic.assert_called_once_with("my_topic")

    @pytest.mark.asyncio
    async def test_builds_slim_container_explicit_endpoint(self):
        """Explicit endpoint format: slim://host:46357/my_topic."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        mock_transport = MagicMock()
        factory = MagicMock()
        factory.create_transport.return_value = mock_transport

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slim",
                    url="slim://host:46357/my_topic",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            await builder.start()

        factory.create_transport.assert_called_once_with(
            "SLIM",
            endpoint="http://host:46357",
            name="my_topic",
            shared_secret_identity="secret" * 6,
        )
        mock_builder.with_topic.assert_called_once_with("my_topic")

    @pytest.mark.asyncio
    async def test_keep_alive_forwarded(self):
        mock_builder = MagicMock()
        mock_builder.with_host.return_value = mock_builder
        mock_builder.with_port.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        await builder.start(keep_alive=True)

        session.start_all_sessions.assert_awaited_once_with(keep_alive=True)

    @pytest.mark.asyncio
    async def test_unknown_transport_skipped(self):
        """Unknown transport types are logged and skipped, not fatal."""
        mock_builder = MagicMock()
        mock_builder.with_host.return_value = mock_builder
        mock_builder.with_port.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(transport="grpc", url="grpc://host:50051/svc"),
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                ),
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        # Should not raise — unknown transport is skipped
        await builder.start()

        # Only the http container should have been registered
        session.add.assert_called_once()
        session.start_all_sessions.assert_awaited_once()


# =========================================================================
# ServeCardPlan
# =========================================================================


class TestServeCardPlan:
    def test_empty_plan_str(self):
        plan = ServeCardPlan()
        assert str(plan) == "No containers to serve."

    def test_plan_str_with_entries(self):
        plan = ServeCardPlan(
            containers=[
                {
                    "session_id": "http-0",
                    "transport": "jsonrpc",
                    "detail": "host=0.0.0.0, port=9999",
                }
            ]
        )
        text = str(plan)
        assert "http-0" in text
        assert "jsonrpc" in text
        assert "0.0.0.0" in text


# =========================================================================
# AppSession.add_a2a_card
# =========================================================================


class TestAppSessionAddA2aCard:
    """Test the convenience method on AppSession returns a CardBuilder."""

    def test_add_a2a_card_returns_card_builder(self):
        from agntcy_app_sdk.app_sessions import AppSession

        session = AppSession()
        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()

        result = session.add_a2a_card(card, handler)
        assert isinstance(result, CardBuilder)

    @pytest.mark.asyncio
    async def test_start_creates_default_factory(self):
        """Calling .start() without .with_factory() auto-creates a factory."""
        from agntcy_app_sdk.app_sessions import AppSession

        session = AppSession()
        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()

        cb = session.add_a2a_card(card, handler)
        assert cb._factory is None

        # Patch start_all_sessions to avoid actually starting anything
        with patch.object(session, "start_all_sessions", new_callable=AsyncMock):
            await cb.start()

        # Factory should have been auto-created
        assert cb._factory is not None

    @pytest.mark.asyncio
    async def test_start_uses_provided_factory(self):
        """Calling .with_factory(f).start() uses the provided factory."""
        from agntcy_app_sdk.app_sessions import AppSession

        session = AppSession()
        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()
        custom_factory = MagicMock()

        cb = session.add_a2a_card(card, handler).with_factory(custom_factory)

        with patch.object(session, "start_all_sessions", new_callable=AsyncMock):
            await cb.start()

        assert cb._factory is custom_factory

    @pytest.mark.asyncio
    async def test_dry_run_returns_plan(self):
        from agntcy_app_sdk.app_sessions import AppSession

        session = AppSession()
        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()

        plan = await session.add_a2a_card(card, handler).dry_run()
        assert isinstance(plan, ServeCardPlan)
        assert len(plan.containers) == 1


# =========================================================================
# Transport aliasing — parse_interface_url
# =========================================================================


class TestTransportAliasing:
    """Verify that alias labels resolve correctly through parse_interface_url."""

    def test_slim_alias_resolves_to_slimpatterns(self):
        """transport='slim' should parse as slimpatterns."""
        iface = AgentInterface(transport="slim", url="slim://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"
        assert result["endpoint"] == "http://localhost:46357"

    def test_slim_extended_alias_resolves_to_slimpatterns(self):
        """transport='slim-extended' should parse as slimpatterns."""
        iface = AgentInterface(transport="slim-extended", url="slim://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"
        assert result["endpoint"] == "http://localhost:46357"

    def test_slim_extended_explicit_endpoint(self):
        """slim-extended with explicit host:port."""
        iface = AgentInterface(
            transport="slim-extended", url="slim://host:46357/my_topic"
        )
        result = parse_interface_url(iface)
        assert result["endpoint"] == "http://host:46357"
        assert result["topic"] == "my_topic"

    def test_nats_alias_resolves_to_natspatterns(self):
        """transport='nats' should parse as natspatterns."""
        iface = AgentInterface(transport="nats", url="nats://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"
        assert result["endpoint"] == "nats://localhost:4222"

    def test_case_insensitive_alias(self):
        """Aliases are case-insensitive."""
        iface = AgentInterface(transport="SLIM-EXTENDED", url="slim://topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "topic"

    def test_case_insensitive_canonical(self):
        """Canonical labels are also case-insensitive."""
        iface = AgentInterface(transport="SLIMPATTERNS", url="slim://my_topic")
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"


# =========================================================================
# Transport aliasing — CardBuilder dry-run
# =========================================================================


class TestCardBuilderAliasedDryRun:
    """Dry-run with aliased transport labels should show canonical names."""

    @pytest.mark.asyncio
    async def test_slim_extended_dry_run(self):
        """slim-extended resolves to slimpatterns in dry-run plan."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="slim-extended",
                    url="slim://my_topic",
                )
            ],
            session=session,
        )

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        assert len(plan.containers) == 1
        entry = plan.containers[0]
        assert entry["session_id"] == "slim-0"
        # Should show canonical transport name
        assert entry["transport"] == "slimpatterns"
        assert "my_topic" in entry["detail"]

    @pytest.mark.asyncio
    async def test_nats_alias_dry_run(self):
        """transport='nats' resolves to natspatterns in dry-run plan."""
        session = MagicMock()
        builder = _make_builder(
            interfaces=[
                AgentInterface(
                    transport="nats",
                    url="nats://my_topic",
                )
            ],
            session=session,
        )

        plan = await builder.dry_run()

        assert plan.containers[0]["transport"] == "natspatterns"


# =========================================================================
# Transport aliasing — CardBuilder container building
# =========================================================================


class TestCardBuilderAliasedBuilds:
    """Non-dry-run with aliases: verify correct builder calls."""

    @pytest.mark.asyncio
    async def test_builds_slim_extended_container(self):
        """slim-extended should build the same as slimpatterns."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        mock_transport = MagicMock()
        factory = MagicMock()
        factory.create_transport.return_value = mock_transport

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slim-extended",
                    url="slim://my_topic",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory)

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            await builder.start()

        factory.create_transport.assert_called_once_with(
            "SLIM",
            endpoint="http://localhost:46357",
            name="my_topic",
            shared_secret_identity="secret" * 6,
        )
        mock_builder.with_topic.assert_called_once_with("my_topic")
        mock_builder.with_session_id.assert_called_once_with("slim-0")


# =========================================================================
# CardBuilder — override
# =========================================================================


class TestCardBuilderOverride:
    """Tests for the .override() fluent method."""

    @pytest.mark.asyncio
    async def test_override_slimrpc_uses_provided_config(self):
        """User's pre-built config is passed to session.add() instead of auto-created."""
        mock_builder = MagicMock()
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                )
            ]
        )
        handler = MagicMock()
        user_config = MagicMock(name="UserSlimRpcConfig")

        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).override("slimrpc", user_config)

        await builder.start()

        # The user's config should have been passed to session.add()
        session.add.assert_called_once_with(user_config)
        mock_builder.with_session_id.assert_called_once_with("slimrpc-0")

    @pytest.mark.asyncio
    async def test_override_slimpatterns_uses_provided_transport(self):
        """User's transport is used instead of factory.create_transport()."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()
        user_transport = MagicMock(name="UserSlimTransport")

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimpatterns",
                    url="slim://my_topic",
                )
            ]
        )
        handler = MagicMock()

        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).override("slimpatterns", user_transport)

        await builder.start()

        # Factory should NOT have been called to create a transport
        factory.create_transport.assert_not_called()
        mock_builder.with_transport.assert_called_once_with(user_transport)
        mock_builder.with_topic.assert_called_once_with("my_topic")

    @pytest.mark.asyncio
    async def test_override_natspatterns_uses_provided_transport(self):
        """User's transport is used instead of factory.create_transport()."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()
        user_transport = MagicMock(name="UserNatsTransport")

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="natspatterns",
                    url="nats://my_topic",
                )
            ]
        )
        handler = MagicMock()

        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).override("natspatterns", user_transport)

        await builder.start()

        factory.create_transport.assert_not_called()
        mock_builder.with_transport.assert_called_once_with(user_transport)
        mock_builder.with_topic.assert_called_once_with("my_topic")

    @pytest.mark.asyncio
    async def test_override_slimrpc_skips_shared_secret_check(self):
        """Override should NOT raise even without SLIM_SHARED_SECRET."""
        mock_builder = MagicMock()
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()
        user_config = MagicMock(name="UserSlimRpcConfig")

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                )
            ]
        )
        handler = MagicMock()

        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).override("slimrpc", user_config)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLIM_SHARED_SECRET", None)
            # Should NOT raise — override bypasses the secret check
            await builder.start()

        session.add.assert_called_once_with(user_config)

    @pytest.mark.asyncio
    async def test_override_slimpatterns_skips_shared_secret_check(self):
        """Override should NOT raise even without SLIM_SHARED_SECRET."""
        mock_builder = MagicMock()
        mock_builder.with_transport.return_value = mock_builder
        mock_builder.with_topic.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()
        user_transport = MagicMock(name="UserSlimTransport")

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimpatterns",
                    url="slim://my_topic",
                )
            ]
        )
        handler = MagicMock()

        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).override("slimpatterns", user_transport)

        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SLIM_SHARED_SECRET", None)
            # Should NOT raise — override bypasses the secret check
            await builder.start()

        mock_builder.with_transport.assert_called_once_with(user_transport)


# =========================================================================
# CardBuilder — skip
# =========================================================================


class TestCardBuilderSkip:
    """Tests for the .skip() fluent method."""

    @pytest.mark.asyncio
    async def test_skip_transport_not_in_plan(self):
        """dry-run with .skip("jsonrpc") should omit that entry."""
        session = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="nats",
                    url="nats://my_topic",
                ),
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                ),
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.skip("jsonrpc")

        plan = await builder.dry_run()

        assert len(plan.containers) == 1
        assert plan.containers[0]["transport"] == "natspatterns"

    @pytest.mark.asyncio
    async def test_skip_transport_not_built(self):
        """Non-dry-run with .skip("slimrpc") should not call session.add() for it."""
        mock_builder = MagicMock()
        mock_builder.with_host.return_value = mock_builder
        mock_builder.with_port.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        factory = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                ),
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                ),
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.with_factory(factory).skip("slimrpc")

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            await builder.start()

        # Only the HTTP container should have been built (slimrpc was skipped)
        session.add.assert_called_once()
        mock_builder.with_session_id.assert_called_once_with("http-1")

    @pytest.mark.asyncio
    async def test_skip_multiple_transports(self):
        """Skip 2 transports, verify only remaining are built."""
        session = MagicMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="slimrpc",
                    url="slim://org/ns/agent",
                ),
                AgentInterface(
                    transport="nats",
                    url="nats://my_topic",
                ),
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                ),
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)
        builder.skip("slimrpc").skip("natspatterns")

        with patch.dict(os.environ, {"SLIM_SHARED_SECRET": "secret" * 6}):
            plan = await builder.dry_run()

        assert len(plan.containers) == 1
        assert plan.containers[0]["transport"] == "jsonrpc"


# =========================================================================
# CardBuilder — fluent chaining
# =========================================================================


class TestCardBuilderFluent:
    """Verify fluent API returns self for chaining."""

    def test_chaining_returns_self(self):
        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()
        session = MagicMock()
        factory = MagicMock()

        builder = CardBuilder(session, card, handler)

        result1 = builder.with_factory(factory)
        assert result1 is builder

        result2 = builder.override("slimrpc", MagicMock())
        assert result2 is builder

        result3 = builder.skip("natspatterns")
        assert result3 is builder

    @pytest.mark.asyncio
    async def test_auto_creates_factory(self):
        """Calling .start() without .with_factory() still works."""
        mock_builder = MagicMock()
        mock_builder.with_host.return_value = mock_builder
        mock_builder.with_port.return_value = mock_builder
        mock_builder.with_session_id.return_value = mock_builder
        mock_builder.build.return_value = MagicMock()

        session = MagicMock()
        session.add.return_value = mock_builder
        session.start_all_sessions = AsyncMock()

        card = _make_card(
            interfaces=[
                AgentInterface(
                    transport="jsonrpc",
                    url="http://0.0.0.0:9999",
                )
            ]
        )
        handler = MagicMock()
        builder = CardBuilder(session, card, handler)

        # No with_factory() call — should auto-create
        await builder.start()

        session.add.assert_called_once()
        session.start_all_sessions.assert_awaited_once()
        # Factory was auto-created
        assert builder._factory is not None


# =========================================================================
# InterfaceTransport
# =========================================================================


class TestInterfaceTransport:
    """Verify InterfaceTransport constants and classmethods."""

    def test_canonical_values(self):
        assert InterfaceTransport.SLIM_RPC == "slimrpc"
        assert InterfaceTransport.SLIM_PATTERNS == "slimpatterns"
        assert InterfaceTransport.NATS_PATTERNS == "natspatterns"
        assert InterfaceTransport.JSONRPC == "jsonrpc"
        assert InterfaceTransport.HTTP == "http"

    def test_alias_values(self):
        assert InterfaceTransport.SLIM == "slimpatterns"
        assert InterfaceTransport.NATS == "natspatterns"
        assert InterfaceTransport.SLIM_EXTENDED == "slimpatterns"

    def test_all_types_includes_canonical_and_aliases(self):
        labels = InterfaceTransport.all_types()
        # Canonical
        assert "slimrpc" in labels
        assert "slimpatterns" in labels
        assert "natspatterns" in labels
        assert "jsonrpc" in labels
        assert "http" in labels
        # Aliases
        assert "slim" in labels
        assert "nats" in labels
        assert "slim-extended" in labels

    def test_canonical_types_excludes_aliases(self):
        labels = InterfaceTransport.canonical_types()
        assert "slimrpc" in labels
        assert "slimpatterns" in labels
        assert "natspatterns" in labels
        assert "jsonrpc" in labels
        assert "http" in labels
        # Aliases should NOT be in canonical set
        assert "slim" not in labels
        assert "nats" not in labels
        assert "slim-extended" not in labels

    def test_label_usable_in_agent_interface(self):
        """Labels work directly in AgentInterface construction."""
        iface = AgentInterface(
            transport=InterfaceTransport.SLIM_PATTERNS,
            url="slim://my_topic",
        )
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"

    def test_alias_label_usable_in_agent_interface(self):
        """Alias labels also work in AgentInterface construction."""
        iface = AgentInterface(
            transport=InterfaceTransport.SLIM_EXTENDED,
            url="slim://my_topic",
        )
        result = parse_interface_url(iface)
        assert result["topic"] == "my_topic"

    def test_importable_from_package(self):
        """InterfaceTransport is importable from the top-level package."""
        from agntcy_app_sdk import InterfaceTransport as TL

        assert TL.SLIM_RPC == "slimrpc"

    def test_card_builder_importable_from_package(self):
        """CardBuilder is importable from the top-level package."""
        from agntcy_app_sdk import CardBuilder as CB

        assert CB is CardBuilder
