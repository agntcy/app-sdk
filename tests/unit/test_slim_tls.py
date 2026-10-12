# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""``tls_insecure`` selects plaintext vs TLS for every SLIM dataplane connection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import slim_bindings
from a2a.types import AgentCard

from agntcy_app_sdk.semantic.a2a.client.config import (
    ClientConfig,
    SlimRpcConfig,
    SlimTransportConfig,
)
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.server.srpc import (
    A2ASlimRpcServerConfig,
    A2ASRPCServerHandler,
    SlimRpcConnectionConfig,
)
from agntcy_app_sdk.transport.slim import common
from agntcy_app_sdk.transport.slim.common import new_client_config
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

pytest_plugins = "pytest_asyncio"

# Test-only placeholder, not a credential.
_TEST_SECRET = "x" * 32


class _Stop(Exception):
    """Raised by the patched config builder to end ``setup()`` early."""


def _stop_at_connect():
    """A ``new_client_config`` stand-in that records its call, then stops."""
    return MagicMock(side_effect=_Stop)


class TestNewClientConfig:
    def test_insecure_is_plaintext(self):
        cfg = new_client_config("http://localhost:46357", insecure=True)
        assert cfg.tls.insecure is True

    def test_secure_is_verified_tls13(self):
        cfg = new_client_config("https://localhost:46357", insecure=False)
        assert cfg.tls.insecure is False
        assert cfg.tls.insecure_skip_verify is False
        assert cfg.tls.include_system_ca_certs_pool is True
        assert cfg.tls.tls_version == "tls1.3"


class TestSlimRpcServerHonorsTlsInsecure:
    @staticmethod
    def _handler(**connection) -> A2ASRPCServerHandler:
        return A2ASRPCServerHandler(
            A2ASlimRpcServerConfig(
                agent_card=AgentCard(name="agent", version="1.0.0"),
                request_handler=MagicMock(),
                connection=SlimRpcConnectionConfig(
                    identity="org/ns/agent", shared_secret=_TEST_SECRET, **connection
                ),
            )
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tls_insecure", [True, False])
    async def test_setup_passes_flag(self, tls_insecure):
        handler = self._handler(
            endpoint="https://slim.example:443", tls_insecure=tls_insecure
        )
        builder = _stop_at_connect()

        with (
            patch(
                "agntcy_app_sdk.semantic.a2a.server.srpc.get_or_init_slim_service",
                return_value=MagicMock(),
            ),
            patch("agntcy_app_sdk.semantic.a2a.server.srpc.new_client_config", builder),
        ):
            with pytest.raises(_Stop):
                await handler.setup()

        builder.assert_called_once_with(
            "https://slim.example:443/", insecure=tls_insecure
        )

    def test_default_stays_plaintext(self):
        assert SlimRpcConnectionConfig("org/ns/a", _TEST_SECRET).tls_insecure is True


class TestSlimRpcClientHonorsTlsInsecure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tls_insecure", [True, False])
    async def test_factory_passes_flag(self, tls_insecure):
        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="org",
                group="ns",
                name="client",
                slim_url="https://slim.example:443",
                secret=_TEST_SECRET,
                tls_insecure=tls_insecure,
            ),
        )
        factory = A2AClientFactory(config)
        builder = _stop_at_connect()

        with (
            patch(
                "agntcy_app_sdk.transport.slim.common.get_or_init_slim_service",
                return_value=MagicMock(),
            ),
            patch("slim_bindings.uniffi_set_event_loop", MagicMock()),
            patch("agntcy_app_sdk.transport.slim.common.new_client_config", builder),
        ):
            with pytest.raises(_Stop):
                await factory._build_slimrpc_if_needed()

        builder.assert_called_once_with(
            "https://slim.example:443/", insecure=tls_insecure
        )

    def test_default_stays_plaintext(self):
        assert SlimRpcConfig(namespace="o", group="n", name="c").tls_insecure is True


class TestSlimTransportHonorsTlsInsecure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tls_insecure", [True, False])
    async def test_instance_passes_flag(self, tls_insecure):
        builder = _stop_at_connect()
        service = MagicMock()
        service.connect_async = AsyncMock()

        with (
            patch.object(common, "global_slim", None),
            patch.object(common, "global_slim_service", None),
            patch.object(common, "get_or_init_slim_service", return_value=service),
            patch.object(common, "shared_secret_identity", return_value=(None, None)),
            patch.object(common, "new_client_config", builder),
        ):
            with pytest.raises(_Stop):
                await common.get_or_create_slim_instance(
                    MagicMock(),
                    "https://slim.example:443",
                    slim_insecure_client=tls_insecure,
                    shared_secret=_TEST_SECRET,
                )

        builder.assert_called_once_with(
            "https://slim.example:443", insecure=tls_insecure
        )


class TestSharedConnectionKeepsItsTlsSetting:
    """All ``SLIMTransport``s in a process share one connection.  A later one
    asking for a different ``tls_insecure`` must be refused, not silently
    handed a connection opened the other way."""

    @staticmethod
    async def _open(
        *,
        insecure: bool,
        builder: MagicMock,
        endpoint: str = "https://slim.example:443",
    ):
        return await common.get_or_create_slim_instance(
            MagicMock(),
            endpoint,
            slim_insecure_client=insecure,
            shared_secret=_TEST_SECRET,
        )

    @staticmethod
    def _patched(builder: MagicMock):
        service = MagicMock()
        service.connect_async = AsyncMock(return_value=7)
        app = MagicMock()
        app.subscribe_async = AsyncMock()
        service.create_app.return_value = app
        return service, (
            patch.object(common, "global_slim", None),
            patch.object(common, "global_slim_service", None),
            patch.object(common, "global_connection_id", None),
            patch.object(common, "global_slim_insecure", None),
            patch.object(common, "global_slim_endpoint", None),
            patch.object(common, "get_or_init_slim_service", return_value=service),
            patch.object(common, "shared_secret_identity", return_value=(None, None)),
            patch.object(common, "new_client_config", builder),
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("first", [True, False])
    async def test_conflicting_second_request_is_refused(self, first):
        builder = MagicMock(return_value="cfg")
        service, patches = self._patched(builder)

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await self._open(insecure=first, builder=builder)

            with pytest.raises(ValueError, match="tls_insecure"):
                await self._open(insecure=not first, builder=builder)

        # The refused request never opened (or re-opened) a connection.
        builder.assert_called_once()
        service.connect_async.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("flag", [True, False])
    async def test_matching_second_request_reuses_the_connection(self, flag):
        builder = MagicMock(return_value="cfg")
        service, patches = self._patched(builder)

        from contextlib import ExitStack

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            first = await self._open(insecure=flag, builder=builder)
            second = await self._open(insecure=flag, builder=builder)

        assert first == second
        builder.assert_called_once()
        service.connect_async.assert_awaited_once()


class TestSharedConnectionKeepsItsEndpoint:
    """A second transport asking for a different dataplane is refused instead
    of being silently attached to the first one."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "other",
        [
            "https://other.example:443",
            "https://slim.example:444",
            "http://slim.example:443",
            "https://slim.example",  # same host, port not spelled out: as written
        ],
    )
    async def test_different_endpoint_is_refused(self, other):
        from contextlib import ExitStack

        builder = MagicMock(return_value="cfg")
        service, patches = TestSharedConnectionKeepsItsTlsSetting._patched(builder)
        open_ = TestSharedConnectionKeepsItsTlsSetting._open

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            await open_(insecure=False, builder=builder)

            with pytest.raises(ValueError, match="same endpoint") as exc:
                await open_(insecure=False, builder=builder, endpoint=other)

        # Both endpoints are named, so the mistake is findable from the message.
        assert "https://slim.example:443" in str(exc.value)
        assert other in str(exc.value)
        # The refused request neither dialed nor built another config.
        builder.assert_called_once()
        service.connect_async.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "again",
        ["https://slim.example:443", "https://slim.example:443/"],
    )
    async def test_same_endpoint_reuses_the_connection(self, again):
        """Identical endpoints, with or without a trailing slash, are one
        dataplane and share the connection."""
        from contextlib import ExitStack

        builder = MagicMock(return_value="cfg")
        service, patches = TestSharedConnectionKeepsItsTlsSetting._patched(builder)
        open_ = TestSharedConnectionKeepsItsTlsSetting._open

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            first = await open_(insecure=False, builder=builder)
            second = await open_(insecure=False, builder=builder, endpoint=again)

        assert first == second
        builder.assert_called_once()
        service.connect_async.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_endpoint_is_recorded_only_after_connecting(self):
        """A failed first connect must not pin the endpoint, or a retry against
        the right dataplane would be refused."""
        from contextlib import ExitStack

        builder = MagicMock(return_value="cfg")
        service, patches = TestSharedConnectionKeepsItsTlsSetting._patched(builder)
        service.connect_async = AsyncMock(side_effect=RuntimeError("down"))

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            with pytest.raises(RuntimeError, match="down"):
                await TestSharedConnectionKeepsItsTlsSetting._open(
                    insecure=False, builder=builder
                )

            assert common.global_slim_endpoint is None

    @pytest.mark.asyncio
    async def test_concurrent_setups_cannot_bypass_the_guard(self):
        """Two setups awaited together must not both connect: the second has
        to see the first one's connection and be refused."""
        import asyncio
        from contextlib import ExitStack

        builder = MagicMock(return_value="cfg")
        service, patches = TestSharedConnectionKeepsItsTlsSetting._patched(builder)

        async def slow_connect(_cfg):
            await asyncio.sleep(0.05)
            return 7

        service.connect_async = AsyncMock(side_effect=slow_connect)
        open_ = TestSharedConnectionKeepsItsTlsSetting._open

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            results = await asyncio.gather(
                open_(insecure=False, builder=builder),
                open_(
                    insecure=False,
                    builder=builder,
                    endpoint="https://other.example:443",
                ),
                return_exceptions=True,
            )
            assert common.global_slim_endpoint == "https://slim.example:443"

        assert not isinstance(results[0], BaseException)
        assert isinstance(results[1], ValueError)
        assert "same endpoint" in str(results[1])
        service.connect_async.assert_awaited_once()


class TestSchemeDisagreement:
    """slim-bindings decides plaintext vs TLS from the URL scheme, so:

    * TLS requested over ``http://`` would silently be plaintext -> refused;
    * plaintext requested over ``https://`` is what the caller asked for ->
      allowed, with a warning.
    """

    @pytest.mark.parametrize("endpoint", ["http://localhost:46357", "HTTP://h:1/"])
    def test_tls_over_http_is_refused(self, endpoint):
        with patch.object(slim_bindings, "new_secure_client_config") as secure:
            with pytest.raises(ValueError, match="tls_insecure=False") as exc:
                new_client_config(endpoint, insecure=False)

        assert endpoint in str(exc.value)
        assert "https://" in str(exc.value)
        secure.assert_not_called()  # nothing was built

    @pytest.mark.parametrize(
        ("endpoint", "insecure", "warns"),
        [
            ("http://localhost:46357", True, False),
            ("https://slim.example:443", False, False),
            ("https://slim.example:443/", True, True),
            ("localhost:46357", False, False),  # no scheme: nothing to compare
        ],
    )
    def test_allowed_combinations(self, endpoint, insecure, warns):
        with patch.object(common, "logger") as log:
            cfg = new_client_config(endpoint, insecure=insecure)

        assert cfg.tls.insecure is insecure
        if warns:
            log.warning.assert_called_once()
            assert endpoint in log.warning.call_args.args
        else:
            log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_refusal_happens_before_anything_is_created(self):
        """A refused combination must not leave a SLIM App behind."""
        service = MagicMock()

        with (
            patch.object(common, "global_slim", None),
            patch.object(common, "global_slim_service", None),
            patch.object(common, "global_slim_insecure", None),
            patch.object(common, "get_or_init_slim_service", return_value=service),
        ):
            with pytest.raises(ValueError, match="https://"):
                await common.get_or_create_slim_instance(
                    MagicMock(),
                    "http://localhost:46357",
                    slim_insecure_client=False,
                    shared_secret=_TEST_SECRET,
                )

        service.create_app.assert_not_called()
        service.connect_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_slimrpc_server_refuses_tls_over_http(self):
        handler = TestSlimRpcServerHonorsTlsInsecure._handler(
            endpoint="http://localhost:46357", tls_insecure=False
        )
        with patch(
            "agntcy_app_sdk.semantic.a2a.server.srpc.get_or_init_slim_service",
            return_value=MagicMock(),
        ):
            with pytest.raises(ValueError, match="https://"):
                await handler.setup()

    @pytest.mark.asyncio
    async def test_slimrpc_client_refuses_tls_over_http(self):
        factory = A2AClientFactory(
            ClientConfig(
                slimrpc_config=SlimRpcConfig(
                    namespace="org",
                    group="ns",
                    name="client",
                    slim_url="http://localhost:46357",
                    secret=_TEST_SECRET,
                    tls_insecure=False,
                ),
            )
        )
        with (
            patch(
                "agntcy_app_sdk.transport.slim.common.get_or_init_slim_service",
                return_value=MagicMock(),
            ),
            patch("slim_bindings.uniffi_set_event_loop", MagicMock()),
        ):
            with pytest.raises(ValueError, match="https://"):
                await factory._build_slimrpc_if_needed()


class TestSLIMTransportPassesTlsInsecure:
    """A real ``SLIMTransport`` must hand its flag to the connection helper.

    ``get_or_create_slim_instance`` is patched, so nothing is dialed.  A live
    TLS dataplane is not part of this suite: the local one is plaintext.
    """

    @staticmethod
    async def _connect(endpoint: str, tls_insecure: bool):
        transport = SLIMTransport(
            routable_name="org/ns/agent",
            endpoint=endpoint,
            shared_secret_identity=_TEST_SECRET,
            tls_insecure=tls_insecure,
        )
        connected = AsyncMock(return_value=(MagicMock(), MagicMock(), 7))
        with patch(
            "agntcy_app_sdk.transport.slim.transport.get_or_create_slim_instance",
            connected,
        ):
            await transport.setup()
        return transport, connected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("endpoint", "tls_insecure"),
        [("http://localhost:46357", True), ("https://slim.example:443", False)],
    )
    async def test_setup_passes_flag(self, endpoint, tls_insecure):
        transport, connected = await self._connect(endpoint, tls_insecure)

        assert transport._tls_insecure is tls_insecure
        connected.assert_awaited_once()
        assert connected.await_args.kwargs["slim_insecure_client"] is tls_insecure
        assert connected.await_args.kwargs["slim_endpoint"] == endpoint

    @pytest.mark.asyncio
    async def test_factory_config_false_is_not_dropped(self):
        """``False`` must survive the factory's ``v is not None`` filter."""
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="https://slim.example:443",
                name="org/ns/agent",
                shared_secret_identity=_TEST_SECRET,
                tls_insecure=False,
            )
        )
        factory = A2AClientFactory(config)
        connected = AsyncMock(return_value=(MagicMock(), MagicMock(), 7))

        with patch(
            "agntcy_app_sdk.transport.slim.transport.get_or_create_slim_instance",
            connected,
        ):
            transport = await factory._build_patterns_transport("slimpatterns")

        assert isinstance(transport, SLIMTransport)
        assert transport._tls_insecure is False
        assert connected.await_args.kwargs["slim_insecure_client"] is False
