# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""``tls_insecure`` selects plaintext vs TLS for every SLIM dataplane connection."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import slim_bindings
from a2a.helpers import new_text_message
from a2a.types import AgentCard, Role, SendMessageRequest
from agntcy.identity.service.v1alpha1.auth_service_pb2 import ExtAuthzRequest
from slima2a.handler import SRPCHandler
from slima2a.types.v1.a2a_pb2_slimrpc import A2AServiceServicer

from agntcy_app_sdk.semantic.a2a.card_utils import IDENTITY_AUTH_SCHEME
from agntcy_app_sdk.semantic.a2a.client.config import (
    ClientConfig,
    SlimRpcConfig,
    SlimTransportConfig,
)
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    _AUTHORIZE_TIMEOUT,
)
from agntcy_app_sdk.semantic.a2a.server.srpc import (
    A2ASlimRpcServerConfig,
    A2ASRPCServerHandler,
    SlimRpcConnectionConfig,
    _IdentityCheckedSRPCHandler,
)
from agntcy_app_sdk.transport.slim import common
from agntcy_app_sdk.transport.slim.common import new_client_config
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

pytest_plugins = "pytest_asyncio"

_SRPC = "agntcy_app_sdk.semantic.a2a.server.srpc"

# Test-only placeholder, not a credential.
_TEST_SECRET = "x" * 32


class _Stop(Exception):
    """Raised by the patched config builder to end ``setup()`` early."""


def _stop_at_connect() -> MagicMock:
    """A ``new_client_config`` stand-in that records its call, then stops."""
    return MagicMock(side_effect=_Stop)


class TestNewClientConfig:
    def test_insecure_is_plaintext(self) -> None:
        cfg = new_client_config("http://localhost:46357", insecure=True)
        assert cfg.tls.insecure is True

    def test_secure_is_verified_tls13(self) -> None:
        cfg = new_client_config("https://localhost:46357", insecure=False)
        assert cfg.tls.insecure is False
        assert cfg.tls.insecure_skip_verify is False
        assert cfg.tls.include_system_ca_certs_pool is True
        assert cfg.tls.tls_version == "tls1.3"


class TestSlimRpcServerHonorsTlsInsecure:
    @staticmethod
    def _handler(**connection: Any) -> A2ASRPCServerHandler:
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
    async def test_setup_passes_flag(self, tls_insecure: bool) -> None:
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

    def test_default_stays_plaintext(self) -> None:
        assert SlimRpcConnectionConfig("org/ns/a", _TEST_SECRET).tls_insecure is True


class TestSlimRpcServerResolvesSharedSecret:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("env", "expected", "warnings"),
        [("e" * 32, "e" * 32, 0), (None, common.PLACEHOLDER_SHARED_SECRET, 1)],
        ids=["env-var", "placeholder"],
    )
    async def test_none_secret_is_resolved_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        env: str | None,
        expected: str,
        warnings: int,
    ) -> None:
        if env is None:
            monkeypatch.delenv(common.SLIM_SHARED_SECRET_ENV, raising=False)
        else:
            monkeypatch.setenv(common.SLIM_SHARED_SECRET_ENV, env)
        handler = A2ASRPCServerHandler(
            A2ASlimRpcServerConfig(
                agent_card=AgentCard(name="agent", version="1.0.0"),
                request_handler=MagicMock(),
                connection=SlimRpcConnectionConfig(identity="org/ns/agent"),
            )
        )
        service = MagicMock()
        service.connect_async = AsyncMock(return_value=1)
        service.create_app_with_secret.side_effect = _Stop

        with (
            patch(f"{_SRPC}.get_or_init_slim_service", return_value=service),
            patch(f"{_SRPC}.new_client_config", MagicMock()),
            patch.object(common, "logger") as log,
        ):
            with pytest.raises(_Stop):
                await handler.setup()

        assert service.create_app_with_secret.call_args.args[1] == expected
        assert log.warning.call_count == warnings


class TestSlimRpcServerIdentityAuth:
    """With identity auth enabled, the slimrpc server checks every call's token."""

    @staticmethod
    def _enable(monkeypatch: pytest.MonkeyPatch, enabled: bool = True) -> None:
        if enabled:
            monkeypatch.setenv("IDENTITY_AUTH_ENABLED", "true")
            monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
        else:
            monkeypatch.delenv("IDENTITY_AUTH_ENABLED", raising=False)

    @staticmethod
    async def _registered_servicer(handler: A2ASRPCServerHandler) -> Any:
        """Run ``setup()`` against mocks; return the servicer it registered."""
        service = MagicMock()
        service.connect_async = AsyncMock(return_value=1)
        server = MagicMock()
        server.serve_async = AsyncMock()
        server.shutdown_async = AsyncMock()
        with (
            patch(f"{_SRPC}.get_or_init_slim_service", return_value=service),
            patch(f"{_SRPC}.new_client_config"),
            patch(
                f"{_SRPC}.slim_bindings.Server.new_with_connection", return_value=server
            ),
            patch(f"{_SRPC}.add_A2AServiceServicer_to_server") as register,
        ):
            await handler.setup()
            await handler.teardown()
        return register.call_args.args[0]

    @pytest.mark.asyncio
    async def test_enabled_checks_tokens_and_declares_the_scheme(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable(monkeypatch)
        sdk = MagicMock()
        monkeypatch.setattr(f"{_SRPC}.IdentityServiceSdk", lambda: sdk)
        handler = TestSlimRpcServerHonorsTlsInsecure._handler()

        servicer = await self._registered_servicer(handler)

        assert isinstance(servicer, _IdentityCheckedSRPCHandler)
        assert servicer._identity_sdk is sdk
        assert IDENTITY_AUTH_SCHEME in handler.agent_card.security_schemes

    @pytest.mark.asyncio
    async def test_disabled_registers_the_plain_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable(monkeypatch, enabled=False)
        handler = TestSlimRpcServerHonorsTlsInsecure._handler()

        servicer = await self._registered_servicer(handler)

        assert type(servicer) is SRPCHandler
        assert IDENTITY_AUTH_SCHEME not in handler.agent_card.security_schemes

    @pytest.mark.asyncio
    async def test_setup_fails_when_identity_sdk_cannot_be_built(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._enable(monkeypatch)
        monkeypatch.setattr(
            f"{_SRPC}.IdentityServiceSdk", MagicMock(side_effect=KeyError("ROOT_CA"))
        )
        handler = TestSlimRpcServerHonorsTlsInsecure._handler()
        builder = _stop_at_connect()

        with (
            patch(f"{_SRPC}.get_or_init_slim_service", return_value=MagicMock()),
            patch(f"{_SRPC}.new_client_config", builder),
        ):
            with pytest.raises(KeyError):
                await handler.setup()

        builder.assert_not_called()
        assert IDENTITY_AUTH_SCHEME not in handler.agent_card.security_schemes

    @staticmethod
    def _servicer(sdk: MagicMock) -> tuple[_IdentityCheckedSRPCHandler, MagicMock]:
        request_handler = MagicMock()
        request_handler.on_message_send = AsyncMock(
            return_value=new_text_message("Hello", role=Role.ROLE_AGENT)
        )
        servicer = _IdentityCheckedSRPCHandler(
            agent_card=AgentCard(name="agent", version="1.0.0"),
            request_handler=request_handler,
            identity_sdk=sdk,
        )
        return servicer, request_handler

    @staticmethod
    def _context(metadata: dict[str, str]) -> MagicMock:
        context = MagicMock()
        context.metadata.return_value = metadata
        context.session_id.return_value = "s"
        return context

    @staticmethod
    def _request() -> SendMessageRequest:
        return SendMessageRequest(message=new_text_message("Hi", role=Role.ROLE_USER))

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["Authorization", "authorization"])
    async def test_valid_token_reaches_the_agent(self, key: str) -> None:
        sdk = MagicMock()
        servicer, request_handler = self._servicer(sdk)

        reply = await servicer.SendMessage(
            self._request(), self._context({key: "Bearer good"})
        )

        assert reply.message.parts[0].text == "Hello"
        request_handler.on_message_send.assert_awaited_once()
        sdk.get_auth_service.return_value.ExtAuthz.assert_called_once_with(
            ExtAuthzRequest(access_token="good"), timeout=_AUTHORIZE_TIMEOUT
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("metadata", "code_name"),
        [
            ({}, "UNAUTHENTICATED"),
            ({"Authorization": "Basic abc"}, "UNAUTHENTICATED"),
            ({"Authorization": "Bearer bad"}, "PERMISSION_DENIED"),
        ],
    )
    async def test_rejected_call_does_not_reach_the_agent(
        self, metadata: dict[str, str], code_name: str
    ) -> None:
        sdk = MagicMock()
        sdk.get_auth_service.return_value.ExtAuthz.side_effect = RuntimeError("denied")
        servicer, request_handler = self._servicer(sdk)

        with pytest.raises(slim_bindings.RpcError) as excinfo:
            await servicer.SendMessage(self._request(), self._context(metadata))

        error: Any = excinfo.value
        assert error.code == getattr(slim_bindings.RpcCode, code_name)
        assert "denied" not in error.message
        request_handler.on_message_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_rejected_stream_yields_nothing(self) -> None:
        servicer, request_handler = self._servicer(MagicMock())

        with pytest.raises(slim_bindings.RpcError) as excinfo:
            _ = [
                e
                async for e in servicer.SendStreamingMessage(
                    self._request(), self._context({})
                )
            ]

        error: Any = excinfo.value
        assert error.code == slim_bindings.RpcCode.UNAUTHENTICATED
        request_handler.on_message_send_stream.assert_not_called()

    def test_every_rpc_is_checked(self) -> None:
        rpcs = [n for n in vars(A2AServiceServicer) if not n.startswith("_")]
        assert rpcs
        for name in rpcs:
            assert name in vars(_IdentityCheckedSRPCHandler), name


class TestSlimRpcClientHonorsTlsInsecure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tls_insecure", [True, False])
    async def test_factory_passes_flag(self, tls_insecure: bool) -> None:
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

    def test_default_stays_plaintext(self) -> None:
        assert SlimRpcConfig(namespace="o", group="n", name="c").tls_insecure is True


class TestSlimTransportHonorsTlsInsecure:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("tls_insecure", [True, False])
    async def test_instance_passes_flag(self, tls_insecure: bool) -> None:
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
    ) -> tuple[slim_bindings.Service, slim_bindings.App, int | None]:
        return await common.get_or_create_slim_instance(
            MagicMock(),
            endpoint,
            slim_insecure_client=insecure,
            shared_secret=_TEST_SECRET,
        )

    @staticmethod
    def _patched(
        builder: MagicMock,
    ) -> tuple[MagicMock, tuple[AbstractContextManager[Any], ...]]:
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
    async def test_conflicting_second_request_is_refused(self, first: bool) -> None:
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
    async def test_matching_second_request_reuses_the_connection(
        self, flag: bool
    ) -> None:
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
    async def test_different_endpoint_is_refused(self, other: str) -> None:
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
    async def test_same_endpoint_reuses_the_connection(self, again: str) -> None:
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
    async def test_endpoint_is_recorded_only_after_connecting(self) -> None:
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
    async def test_concurrent_setups_cannot_bypass_the_guard(self) -> None:
        """Two setups awaited together must not both connect: the second has
        to see the first one's connection and be refused."""
        import asyncio
        from contextlib import ExitStack

        builder = MagicMock(return_value="cfg")
        service, patches = TestSharedConnectionKeepsItsTlsSetting._patched(builder)

        async def slow_connect(_cfg: object) -> int:
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
    def test_tls_over_http_is_refused(self, endpoint: str) -> None:
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
    def test_allowed_combinations(
        self, endpoint: str, insecure: bool, warns: bool
    ) -> None:
        with patch.object(common, "logger") as log:
            cfg = new_client_config(endpoint, insecure=insecure)

        assert cfg.tls.insecure is insecure
        if warns:
            log.warning.assert_called_once()
            assert endpoint in log.warning.call_args.args
        else:
            log.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_refusal_happens_before_anything_is_created(self) -> None:
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
    async def test_slimrpc_server_refuses_tls_over_http(self) -> None:
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
    async def test_slimrpc_client_refuses_tls_over_http(self) -> None:
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
    async def _connect(
        endpoint: str, tls_insecure: bool
    ) -> tuple[SLIMTransport, AsyncMock]:
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
    async def test_setup_passes_flag(self, endpoint: str, tls_insecure: bool) -> None:
        transport, connected = await self._connect(endpoint, tls_insecure)

        assert transport._tls_insecure is tls_insecure
        connected.assert_awaited_once()
        assert connected.await_args is not None
        assert connected.await_args.kwargs["slim_insecure_client"] is tls_insecure
        assert connected.await_args.kwargs["slim_endpoint"] == endpoint

    @pytest.mark.asyncio
    async def test_factory_config_false_is_not_dropped(self) -> None:
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
        assert connected.await_args is not None
        assert connected.await_args.kwargs["slim_insecure_client"] is False
