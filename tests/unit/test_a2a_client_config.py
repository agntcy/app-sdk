# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AgentCard-centric A2A client stack:
ClientConfig, PatternsClientTransport, A2AExperimentalClient, A2AClientFactory.

Written against the a2a-sdk 1.x API: protobuf types, ``supported_interfaces``
(list order = server preference), ``before`` / ``after`` interceptors and
``StreamResponse`` events.
"""

import asyncio
import base64
import dataclasses
import json
import warnings
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from a2a.client.base_client import BaseClient
from a2a.client.client import Client, ClientCallContext
from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.errors import A2AClientError
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import (
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    GetExtendedAgentCardRequest,
    GetTaskRequest,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import InternalError, TaskNotFoundError
from slima2a.client_transport import SRPCTransport

from agntcy_app_sdk.common import auth
from agntcy_app_sdk.semantic.a2a.card_utils import declare_identity_auth
from agntcy_app_sdk.semantic.a2a.client.config import (
    ClientConfig,
    NatsTransportConfig,
    SlimRpcConfig,
    SlimTransportConfig,
)
from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
    A2ABroadcastError,
    A2ABroadcastTimeoutError,
    A2AExperimentalClient,
)
from agntcy_app_sdk.semantic.a2a.client.factory import (
    A2AClientFactory,
    _produce_patterns,
    _produce_slimrpc,
)
from agntcy_app_sdk.semantic.a2a.client.transports import (
    PatternsClientTransport,
    _parse_topic_from_url,
)
from agntcy_app_sdk.semantic.a2a.transport_types import SENDER_HEADER

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iface(binding: str, url: str) -> AgentInterface:
    return AgentInterface(protocol_binding=binding, url=url)


def _make_agent_card(
    interfaces: list[AgentInterface] | None = None,
    name: str = "test-agent",
) -> AgentCard:
    """Create a minimal AgentCard for testing.

    Without *interfaces* the card advertises a single JSONRPC endpoint.
    """
    if interfaces is None:
        interfaces = [_iface("JSONRPC", "http://localhost:8080")]
    return AgentCard(
        name=name,
        version="1.0",
        description="Test agent",
        default_input_modes=["text"],
        default_output_modes=["text"],
        supported_interfaces=interfaces,
    )


def _make_mock_transport(transport_type: str = "SLIM") -> MagicMock:
    """Create a mock BaseTransport."""
    transport = MagicMock()
    transport.type.return_value = transport_type
    transport.setup = AsyncMock()
    transport.close = AsyncMock()
    transport.request = AsyncMock()
    transport.request_stream = MagicMock()
    transport.gather = AsyncMock()
    transport.gather_stream = MagicMock()
    transport.start_conversation = AsyncMock()
    transport.start_streaming_conversation = MagicMock()
    return transport


def _user_request(text: str = "Hi") -> SendMessageRequest:
    return SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))


def _message_result(text: str = "Hello") -> dict[str, Any]:
    """ProtoJSON ``result`` carrying an agent message."""
    return {
        "message": {
            "messageId": str(uuid4()),
            "role": "ROLE_AGENT",
            "parts": [{"text": text}],
        }
    }


def _status_result(
    state: str = "TASK_STATE_WORKING", text: str = "tok"
) -> dict[str, Any]:
    """ProtoJSON ``result`` carrying a task status update."""
    return {
        "statusUpdate": {
            "taskId": "task-1",
            "contextId": "ctx-1",
            "status": {
                "state": state,
                "message": {
                    "messageId": str(uuid4()),
                    "role": "ROLE_AGENT",
                    "parts": [{"text": text}],
                },
            },
        }
    }


def _rpc_response(
    result: dict[str, Any] | None = None,
    *,
    type_: str = "A2AResponse",
    status_code: int = 200,
    error: dict[str, Any] | None = None,
    sender: str | None = None,
) -> MagicMock:
    """Create a mock transport response with a JSON-RPC payload.

    *sender* sets the ``x-a2a-sender`` header an SDK server stamps on replies.
    """
    resp = MagicMock()
    payload: dict[str, Any] = {"jsonrpc": "2.0", "id": "1"}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result if result is not None else _message_result()
    resp.payload = json.dumps(payload).encode("utf-8")
    resp.status_code = status_code
    resp.type = type_
    resp.headers = {SENDER_HEADER: sender} if sender else {}
    return resp


def _async_gen(*items: Any) -> Callable[..., AsyncIterator[Any]]:
    """Return a factory for an async generator yielding *items*."""

    async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for item in items:
            yield item

    return _gen


# Error reply as a failing recipient's server handler would send it.
_RPC_ERROR = {"code": -32603, "message": "boom"}

# Upper bound for calls that must return promptly.  Generous so slow CI does
# not flake, yet far below the 60s default transport timeout a hang would hit.
_HANG_GUARD_S = 5.0


def _hanging_gen(*items: Any) -> Callable[..., AsyncIterator[Any]]:
    """Like :func:`_async_gen`, but the stream never ends after *items*.

    This mimics a transport still waiting for more replies until its timeout
    expires.  A client that fails to count an error reply as a final then
    blocks here, which the tests turn into a failure via ``asyncio.wait_for``.
    """

    async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for item in items:
            yield item
        await asyncio.Event().wait()

    return _gen


def _timeout_gen(*items: Any) -> Callable[..., AsyncIterator[Any]]:
    """Like :func:`_async_gen`, then raise ``TimeoutError`` as SLIM does."""

    async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
        for item in items:
            yield item
        raise asyncio.TimeoutError("Timeout while waiting for replies")

    return _gen


async def _drain(
    stream: AsyncIterator[StreamResponse],
) -> tuple[list[StreamResponse], A2ABroadcastError | None]:
    """Consume a broadcast stream; return ``(events, A2ABroadcastError | None)``."""
    events: list[StreamResponse] = []
    error: A2ABroadcastError | None = None
    try:
        async for event in stream:
            events.append(event)
    except A2ABroadcastError as exc:
        error = exc
    return events, error


def _sent_rpc(mock_call: MagicMock) -> dict[str, Any]:
    """Decode the JSON-RPC envelope from the transport ``Message`` that was sent."""
    message = mock_call.call_args.args[1]
    rpc: dict[str, Any] = json.loads(message.payload)
    return rpc


class _RecordingInterceptor(ClientCallInterceptor):
    """Interceptor recording ``before`` / ``after`` calls.

    Subclasses the real ``ClientCallInterceptor`` ABC so the tests verify the
    actual a2a-sdk 1.x interface contract.
    """

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.before_calls: list[BeforeArgs] = []
        self.after_calls: list[AfterArgs] = []
        self._headers = headers

    async def before(self, args: BeforeArgs) -> None:
        self.before_calls.append(args)
        if self._headers:
            if args.context is None:
                args.context = ClientCallContext()
            params = dict(args.context.service_parameters or {})
            params.update(self._headers)
            args.context.service_parameters = params

    async def after(self, args: AfterArgs) -> None:
        self.after_calls.append(args)


# ---------------------------------------------------------------------------
# Transport config dataclass tests
# ---------------------------------------------------------------------------


class TestTransportConfigs:
    def test_slim_transport_config_requires_fields(self) -> None:
        """SlimTransportConfig should require endpoint and name."""
        cfg = SlimTransportConfig(endpoint="http://localhost:46357", name="a/b/c")
        assert cfg.endpoint == "http://localhost:46357"
        assert cfg.name == "a/b/c"

    def test_slim_transport_config_missing_name_raises(self) -> None:
        factory: Any = SlimTransportConfig
        with pytest.raises(TypeError):
            factory(endpoint="http://localhost:46357")

    def test_nats_transport_config_requires_endpoint(self) -> None:
        cfg = NatsTransportConfig(endpoint="nats://localhost:4222")
        assert cfg.endpoint == "nats://localhost:4222"

    def test_nats_transport_config_missing_endpoint_raises(self) -> None:
        factory: Any = NatsTransportConfig
        with pytest.raises(TypeError):
            factory()

    def test_slim_rpc_config_requires_all_fields(self) -> None:
        cfg = SlimRpcConfig(namespace="agntcy", group="demo", name="client")
        assert cfg.namespace == "agntcy"
        assert cfg.group == "demo"
        assert cfg.name == "client"


# ---------------------------------------------------------------------------
# ClientConfig tests
# ---------------------------------------------------------------------------


class TestClientConfig:
    def test_extends_upstream_config(self) -> None:
        config = ClientConfig()
        assert config.slim_config is None
        assert config.slim_transport is None
        assert config.nats_config is None
        assert config.nats_transport is None
        assert config.slimrpc_config is None
        assert config.slimrpc_channel_factory is None
        # Upstream fields should be present
        assert config.streaming is True

    def test_post_init_default_jsonrpc(self) -> None:
        """Empty config should auto-derive supported_protocol_bindings with JSONRPC."""
        assert ClientConfig().supported_protocol_bindings == ["JSONRPC"]

    def test_post_init_slim_config(self) -> None:
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        assert "JSONRPC" in config.supported_protocol_bindings
        assert "slimpatterns" in config.supported_protocol_bindings

    def test_post_init_slim_transport(self) -> None:
        config = ClientConfig(slim_transport=_make_mock_transport())
        assert "slimpatterns" in config.supported_protocol_bindings

    def test_post_init_nats_config(self) -> None:
        config = ClientConfig(
            nats_config=NatsTransportConfig(endpoint="nats://localhost:4222"),
        )
        assert "natspatterns" in config.supported_protocol_bindings

    def test_post_init_nats_transport(self) -> None:
        config = ClientConfig(nats_transport=_make_mock_transport("NATS"))
        assert "natspatterns" in config.supported_protocol_bindings

    def test_post_init_slimrpc_channel_factory(self) -> None:
        config = ClientConfig(slimrpc_channel_factory=MagicMock())
        assert "slimrpc" in config.supported_protocol_bindings

    def test_post_init_slimrpc_config(self) -> None:
        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="agntcy", group="demo", name="client"
            ),
        )
        assert "slimrpc" in config.supported_protocol_bindings

    def test_post_init_multiple_transports(self) -> None:
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
            nats_config=NatsTransportConfig(endpoint="nats://localhost:4222"),
            slimrpc_channel_factory=MagicMock(),
        )
        assert config.supported_protocol_bindings == [
            "JSONRPC",
            "slimpatterns",
            "natspatterns",
            "slimrpc",
        ]

    def test_explicit_bindings_not_overridden(self) -> None:
        """If the user sets supported_protocol_bindings, __post_init__ keeps them."""
        config = ClientConfig(
            supported_protocol_bindings=["custom_transport"],
            slim_transport=_make_mock_transport(),
        )
        assert config.supported_protocol_bindings == ["custom_transport"]

    def test_explicit_bindings_are_normalised(self) -> None:
        """Casing and aliases are normalised to what the upstream factory expects."""
        config = ClientConfig(supported_protocol_bindings=["jsonrpc", "SLIM", "nats"])
        assert config.supported_protocol_bindings == [
            "JSONRPC",
            "slimpatterns",
            "natspatterns",
        ]

    def test_deprecated_supported_transports_is_honoured_with_warning(self) -> None:
        """The a2a-sdk 0.3 field name still works, but warns."""
        with pytest.warns(DeprecationWarning, match="supported_transports"):
            config = ClientConfig(
                supported_transports=["custom_transport"],
                slim_transport=_make_mock_transport(),
            )
        assert config.supported_protocol_bindings == ["custom_transport"]

    def test_deprecated_supported_transports_mirrors_bindings(self) -> None:
        """Reading the old attribute keeps working (mirrors the new field)."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config = ClientConfig(slim_transport=_make_mock_transport())
        assert config.supported_transports == config.supported_protocol_bindings

    def test_replace_with_deprecated_transports_overrides_populated_config(
        self,
    ) -> None:
        """replace(..., supported_transports=...) must win over existing bindings."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            base = ClientConfig(slim_transport=_make_mock_transport())
        assert base.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

        with pytest.warns(DeprecationWarning, match="supported_transports"):
            replaced = dataclasses.replace(base, supported_transports=["jsonrpc"])

        assert replaced.supported_protocol_bindings == ["JSONRPC"]
        assert replaced.supported_transports == ["JSONRPC"]

    def test_assigning_deprecated_transports_after_construction_takes_effect(
        self,
    ) -> None:
        """The factory reads supported_protocol_bindings, so assigning the old
        field must update it rather than be silently ignored."""
        config = ClientConfig(slim_transport=_make_mock_transport())

        with pytest.warns(DeprecationWarning, match="supported_transports"):
            config.supported_transports = ["slim"]

        assert config.supported_protocol_bindings == ["slimpatterns"]
        assert config.supported_transports == ["slimpatterns"]
        # Still a resolved list: a later replace() is not a "both passed".
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            replaced = dataclasses.replace(config)
        assert replaced.supported_protocol_bindings == ["slimpatterns"]

    def test_assigning_bindings_after_construction_does_not_warn(self) -> None:
        config = ClientConfig()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config.supported_protocol_bindings = ["slimrpc"]
            config.use_client_preference = True
        assert config.supported_protocol_bindings == ["slimrpc"]

    def test_both_passed_with_different_values_bindings_win(self) -> None:
        """A fresh config given both fields: the new field wins, with a warning."""
        with pytest.warns(DeprecationWarning, match="supported_transports is ignored"):
            config = ClientConfig(
                supported_protocol_bindings=["jsonrpc", "slim"],
                supported_transports=["custom_transport"],
            )
        assert config.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]
        assert config.supported_transports == config.supported_protocol_bindings

    def test_both_passed_with_equal_values_only_deprecation_warning(self) -> None:
        """Equal after normalisation (case/alias): no 'ignored' message."""
        with pytest.warns(DeprecationWarning, match="deprecated") as record:
            config = ClientConfig(
                supported_protocol_bindings=["JSONRPC", "slimpatterns"],
                supported_transports=["jsonrpc", "slim"],
            )
        assert not any("ignored" in str(w.message) for w in record)
        assert config.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_replace_with_both_fields_is_both_passed(self) -> None:
        """Both overridden in replace(): treated as a fresh 'both passed'."""
        base = ClientConfig(slim_transport=_make_mock_transport())
        with pytest.warns(DeprecationWarning, match="supported_transports is ignored"):
            replaced = dataclasses.replace(
                base,
                supported_protocol_bindings=["custom_a"],
                supported_transports=["custom_b"],
            )
        assert replaced.supported_protocol_bindings == ["custom_a"]

    def test_resolved_bindings_copied_with_list_count_as_user_passed(self) -> None:
        """Copying another config's bindings drops the marker -> 'both passed'."""
        other = ClientConfig(slim_transport=_make_mock_transport())
        with pytest.warns(DeprecationWarning, match="supported_transports is ignored"):
            config = ClientConfig(
                supported_protocol_bindings=list(other.supported_protocol_bindings),
                supported_transports=["custom_transport"],
            )
        assert config.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_resolved_bindings_passed_as_same_object_behave_like_replace(self) -> None:
        """Documented limitation: the same resolved object is treated as replace()."""
        other = ClientConfig(slim_transport=_make_mock_transport())
        with pytest.warns(DeprecationWarning, match="deprecated"):
            config = ClientConfig(
                supported_protocol_bindings=other.supported_protocol_bindings,
                supported_transports=["custom_transport"],
            )
        assert config.supported_protocol_bindings == ["custom_transport"]

    def test_replace_with_bindings_not_overridden_by_stale_mirror(self) -> None:
        """The mirror copied by replace() must not clobber new bindings."""
        base = ClientConfig(slim_transport=_make_mock_transport())

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            replaced = dataclasses.replace(
                base, supported_protocol_bindings=["custom_transport"]
            )

        assert replaced.supported_protocol_bindings == ["custom_transport"]
        assert replaced.supported_transports == ["custom_transport"]

    def test_replace_adding_transport_rederives_bindings_without_warning(
        self,
    ) -> None:
        base = ClientConfig(slim_transport=_make_mock_transport())

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            replaced = dataclasses.replace(base, nats_transport=_make_mock_transport())

        assert replaced.supported_protocol_bindings == [
            "JSONRPC",
            "slimpatterns",
            "natspatterns",
        ]
        assert replaced.supported_transports == replaced.supported_protocol_bindings
        assert base.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_replace_on_default_config_adds_slim_config(self) -> None:
        replaced = dataclasses.replace(
            ClientConfig(),
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="default/default/client"
            ),
        )

        assert replaced.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_replace_removing_transport_rederives_bindings(self) -> None:
        base = ClientConfig(
            slim_transport=_make_mock_transport(),
            nats_transport=_make_mock_transport(),
        )

        replaced = dataclasses.replace(base, nats_transport=None)

        assert replaced.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_replace_unrelated_field_keeps_bindings(self) -> None:
        base = ClientConfig(slim_transport=_make_mock_transport())

        replaced = dataclasses.replace(base, streaming=False)

        assert replaced.supported_protocol_bindings == ["JSONRPC", "slimpatterns"]

    def test_replace_keeps_caller_bindings_when_transports_change(self) -> None:
        base = ClientConfig(
            slim_transport=_make_mock_transport(),
            supported_protocol_bindings=["slimpatterns"],
        )

        replaced = dataclasses.replace(base, nats_transport=_make_mock_transport())

        assert replaced.supported_protocol_bindings == ["slimpatterns"]


# ---------------------------------------------------------------------------
# _parse_topic_from_url tests
# ---------------------------------------------------------------------------


class TestParseTopicFromUrl:
    def test_slim_scheme(self) -> None:
        assert _parse_topic_from_url("slim://my_topic") == "my_topic"

    def test_nats_scheme(self) -> None:
        assert _parse_topic_from_url("nats://my_topic") == "my_topic"

    @pytest.mark.parametrize(
        ("url", "topic"),
        [
            ("slim://Weather_Agent_1.0.0", "Weather_Agent_1.0.0"),
            ("slim://Org/NS/Agent", "Org/NS/Agent"),
            ("nats://Weather_Agent", "Weather_Agent"),
            ("slim://Host:46357/Org/NS/Agent", "Org/NS/Agent"),
        ],
    )
    def test_topic_keeps_case(self, url: str, topic: str) -> None:
        assert _parse_topic_from_url(url) == topic

    def test_plain_topic(self) -> None:
        assert _parse_topic_from_url("my_topic") == "my_topic"

    def test_http_url_passthrough(self) -> None:
        """HTTP URLs should pass through unchanged (not a patterns scheme)."""
        assert _parse_topic_from_url("http://localhost:9999") == "http://localhost:9999"

    def test_topic_with_slashes(self) -> None:
        assert (
            _parse_topic_from_url("slim://default/default/agent")
            == "default/default/agent"
        )

    def test_slim_endpoint_with_port(self) -> None:
        assert _parse_topic_from_url("slim://localhost:46357/my_topic") == "my_topic"

    def test_nats_endpoint_with_port(self) -> None:
        assert _parse_topic_from_url("nats://localhost:4222/my_topic") == "my_topic"

    def test_slim_endpoint_with_port_and_slashes(self) -> None:
        assert (
            _parse_topic_from_url("slim://localhost:46357/default/default/agent")
            == "default/default/agent"
        )


# ---------------------------------------------------------------------------
# PatternsClientTransport tests
# ---------------------------------------------------------------------------


class TestPatternsClientTransport:
    def test_create_slim_eager(self) -> None:
        """create() should use slim_transport from config for slim interfaces."""
        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(slim_transport=mock_transport)
        card = _make_agent_card([_iface("slimpatterns", "slim://topic_1")])

        transport = PatternsClientTransport.create(card, "slim://topic_1", config)
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"
        assert transport._agent_card is card

    def test_create_nats_eager(self) -> None:
        mock_transport = _make_mock_transport("NATS")
        config = ClientConfig(nats_transport=mock_transport)
        card = _make_agent_card([_iface("natspatterns", "nats://topic_1")])

        transport = PatternsClientTransport.create(card, "nats://topic_1", config)
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"

    def test_create_no_transport_raises(self) -> None:
        """create() should raise if no pre-built transport is on config."""
        # Only deferred config, no eager transport — sync create() can't handle it
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        card = _make_agent_card([_iface("slimpatterns", "slim://topic_1")])

        with pytest.raises(ValueError, match="No pre-built transport"):
            PatternsClientTransport.create(card, "slim://topic_1", config)

    def test_create_unknown_transport_raises(self) -> None:
        config = ClientConfig(supported_protocol_bindings=["unknown"])
        card = _make_agent_card([_iface("unknown", "topic_1")])

        with pytest.raises(ValueError, match="No pre-built transport"):
            PatternsClientTransport.create(card, "topic_1", config)

    def test_patterns_producer_uses_sdk_config(self) -> None:
        mock_transport = _make_mock_transport("SLIM")
        card = _make_agent_card([_iface("slimpatterns", "slim://topic_1")])

        transport = _produce_patterns(
            card, "slim://topic_1", ClientConfig(slim_transport=mock_transport)
        )

        assert isinstance(transport, PatternsClientTransport)
        assert transport._transport is mock_transport

    def test_slimrpc_producer_uses_channel_factory(self) -> None:
        channel_factory = MagicMock()
        card = _make_agent_card([_iface("slimrpc", "a/b/c")])

        transport = _produce_slimrpc(
            card, "a/b/c", ClientConfig(slimrpc_channel_factory=channel_factory)
        )

        assert isinstance(transport, SRPCTransport)
        channel_factory.assert_called_once_with("a/b/c")

    @pytest.mark.parametrize(
        "config",
        [A2AClientConfig(), ClientConfig()],
        ids=["upstream-config", "no-channel-factory"],
    )
    def test_slimrpc_producer_needs_channel_factory(
        self, config: A2AClientConfig
    ) -> None:
        with pytest.raises(ValueError, match="slimrpc_channel_factory is required"):
            _produce_slimrpc(_make_agent_card(), "a/b/c", config)

    def test_patterns_producer_rejects_upstream_config(self) -> None:
        with pytest.raises(ValueError, match="SDK's ClientConfig"):
            _produce_patterns(_make_agent_card(), "slim://topic_1", A2AClientConfig())

    @pytest.mark.asyncio
    async def test_send_message(self) -> None:
        """send_message sends a JSON-RPC SendMessage and parses the proto reply."""
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(_message_result("Hello"))
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        response = await pct.send_message(_user_request("Hi"))

        assert isinstance(response, SendMessageResponse)
        assert response.HasField("message")
        assert response.message.parts[0].text == "Hello"

        mock_transport.request.assert_called_once()
        assert mock_transport.request.call_args.args[0] == "test_topic"
        rpc = _sent_rpc(mock_transport.request)
        assert rpc["jsonrpc"] == "2.0"
        assert rpc["method"] == "SendMessage"
        assert rpc["params"]["message"]["parts"][0]["text"] == "Hi"
        assert rpc["params"]["message"]["role"] == "ROLE_USER"

    @pytest.mark.asyncio
    async def test_send_message_returns_task_result(self) -> None:
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            {
                "task": {
                    "id": "t1",
                    "contextId": "c1",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                }
            }
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        response = await pct.send_message(_user_request())

        assert response.HasField("task")
        assert response.task.id == "t1"
        assert response.task.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_send_message_forwards_headers(self) -> None:
        """service_parameters become message headers; the version header is added."""
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response()
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        context = ClientCallContext(service_parameters={"X-Custom": "abc"})
        await pct.send_message(_user_request(), context=context)

        sent = mock_transport.request.call_args.args[1]
        assert sent.headers["X-Custom"] == "abc"
        assert sent.headers["A2A-Version"] == "1.0"

    @pytest.mark.asyncio
    async def test_json_rpc_error_raises_matching_a2a_error(self) -> None:
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            error={"code": -32001, "message": "no such task"}
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        with pytest.raises(TaskNotFoundError):
            await pct.get_task(GetTaskRequest(id="missing"))

    @pytest.mark.asyncio
    async def test_forbidden_send_message_returns_agent_message(self) -> None:
        """An identity-auth rejection is surfaced as an agent message."""
        mock_transport = _make_mock_transport()
        resp = MagicMock()
        resp.payload = json.dumps({"error": "forbidden"}).encode("utf-8")
        resp.status_code = 403
        mock_transport.request.return_value = resp
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        response = await pct.send_message(_user_request())

        assert response.HasField("message")
        assert "Forbidden" in response.message.parts[0].text

    @pytest.mark.asyncio
    async def test_get_task(self) -> None:
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            {"id": "t1", "contextId": "c1", "status": {"state": "TASK_STATE_WORKING"}}
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        task = await pct.get_task(GetTaskRequest(id="t1"))

        assert isinstance(task, Task)
        assert task.id == "t1"
        rpc = _sent_rpc(mock_transport.request)
        assert rpc["method"] == "GetTask"
        assert rpc["params"]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_cancel_task(self) -> None:
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            {"id": "t1", "status": {"state": "TASK_STATE_CANCELED"}}
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        task = await pct.cancel_task(CancelTaskRequest(id="t1"))

        assert task.status.state == TaskState.TASK_STATE_CANCELED
        assert _sent_rpc(mock_transport.request)["method"] == "CancelTask"

    @pytest.mark.asyncio
    async def test_send_message_streaming_yields_events_and_stops_at_final(
        self,
    ) -> None:
        """Intermediate A2AStatusUpdate items and the final A2AResponse are yielded."""
        mock_transport = _make_mock_transport()
        mock_transport.request_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(
                    _status_result("TASK_STATE_WORKING"), type_="A2AStatusUpdate"
                ),
                _rpc_response(_status_result("TASK_STATE_COMPLETED", "done")),
                # Anything after the final response must not be read
                _rpc_response(
                    _status_result("TASK_STATE_FAILED"), type_="A2AStatusUpdate"
                ),
            )
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        events = [e async for e in pct.send_message_streaming(_user_request())]

        assert len(events) == 2
        assert all(isinstance(e, StreamResponse) for e in events)
        assert events[0].status_update.status.state == TaskState.TASK_STATE_WORKING
        assert events[1].status_update.status.state == TaskState.TASK_STATE_COMPLETED
        sent = mock_transport.request_stream.call_args.args[1]
        assert json.loads(sent.payload)["method"] == "SendStreamingMessage"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_send_message_streaming_auth_rejection_drops_the_token(
        self, status: int, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A rejection yields the forbidden message, drops the token, and ends."""
        invalidate = MagicMock()
        monkeypatch.setattr(
            "agntcy_app_sdk.semantic.a2a.client.transports.invalidate_access_token",
            invalidate,
        )
        mock_transport = _make_mock_transport()
        mock_transport.request_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(
                    error={"code": -32603, "message": "Authentication failed"},
                    status_code=status,
                ),
                _rpc_response(_status_result("TASK_STATE_COMPLETED", "done")),
            )
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        events = [e async for e in pct.send_message_streaming(_user_request())]

        assert len(events) == 1
        assert "Access Forbidden" in events[0].message.parts[0].text
        invalidate.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_send_message_streaming_falls_back_to_unary(self) -> None:
        """If the transport cannot stream, a single SendMessage is used."""

        async def _unsupported(*_a: Any, **_k: Any) -> AsyncIterator[Any]:
            raise NotImplementedError
            yield  # pragma: no cover

        mock_transport = _make_mock_transport()
        mock_transport.request_stream = MagicMock(side_effect=_unsupported)
        mock_transport.request.return_value = _rpc_response(_message_result("Fallback"))
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        events = [e async for e in pct.send_message_streaming(_user_request())]

        assert len(events) == 1
        assert events[0].message.parts[0].text == "Fallback"
        assert _sent_rpc(mock_transport.request)["method"] == "SendMessage"

    @pytest.mark.asyncio
    async def test_get_extended_agent_card_returns_cached_card(self) -> None:
        """Without an advertised extended card, no request is made."""
        mock_transport = _make_mock_transport()
        card = _make_agent_card()
        pct = PatternsClientTransport(mock_transport, card, "test_topic")

        result = await pct.get_extended_agent_card(GetExtendedAgentCardRequest())

        assert result is card
        mock_transport.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscribe_not_supported(self) -> None:
        pct = PatternsClientTransport(
            _make_mock_transport(), _make_agent_card(), "test_topic"
        )
        with pytest.raises(NotImplementedError):
            async for _ in pct.subscribe(SubscribeToTaskRequest(id="t1")):
                pass

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        mock_transport = _make_mock_transport()
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        await pct.close()
        mock_transport.close.assert_called_once()


# ---------------------------------------------------------------------------
# A2AExperimentalClient tests
# ---------------------------------------------------------------------------


def _make_experimental(
    interceptors: list[ClientCallInterceptor] | None = None,
    inner: MagicMock | None = None,
) -> tuple[A2AExperimentalClient, MagicMock, AgentCard]:
    mock_transport = _make_mock_transport()
    card = _make_agent_card([_iface("slimpatterns", "slim://test_topic")])
    client = A2AExperimentalClient(
        client=inner or MagicMock(),
        agent_card=card,
        transport=mock_transport,
        topic="test_topic",
        interceptors=interceptors,
    )
    return client, mock_transport, card


class TestA2AExperimentalClient:
    def test_properties(self) -> None:
        mock_client = MagicMock()
        experimental, mock_transport, card = _make_experimental(inner=mock_client)

        assert experimental.agent_card is card
        assert experimental.upstream_client is mock_client
        assert experimental.transport is mock_transport
        assert experimental.topic == "test_topic"

    def test_is_client_subclass(self) -> None:
        experimental, _, _ = _make_experimental()
        assert isinstance(experimental, Client)

    def test_experimental_methods_available(self) -> None:
        experimental, _, _ = _make_experimental()

        assert callable(experimental.broadcast_message)
        assert callable(experimental.broadcast_message_streaming)
        assert callable(experimental.start_groupchat)
        assert callable(experimental.start_streaming_groupchat)

    @pytest.mark.asyncio
    async def test_get_extended_agent_card_delegates(self) -> None:
        inner = MagicMock()
        inner.get_extended_agent_card = AsyncMock(return_value="card")
        experimental, _, _ = _make_experimental(inner=inner)

        request = GetExtendedAgentCardRequest()
        result = await experimental.get_extended_agent_card(request)

        assert result == "card"
        inner.get_extended_agent_card.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_delegates(self) -> None:
        inner = MagicMock()
        expected = StreamResponse()
        expected.message.CopyFrom(new_text_message("hello"))

        async def _send(
            request: SendMessageRequest, *, context: ClientCallContext | None = None
        ) -> AsyncIterator[StreamResponse]:
            yield expected

        inner.send_message = _send
        experimental, _, _ = _make_experimental(inner=inner)

        events = [e async for e in experimental.send_message(_user_request())]
        assert events == [expected]

    @pytest.mark.asyncio
    async def test_close_closes_inner_client(self) -> None:
        inner = MagicMock()
        inner.close = AsyncMock()
        experimental, _, _ = _make_experimental(inner=inner)

        await experimental.close()
        inner.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_interceptor_registers_on_both_clients(self) -> None:
        inner = MagicMock()
        inner.add_interceptor = AsyncMock()
        experimental, _, _ = _make_experimental(inner=inner)
        interceptor = _RecordingInterceptor()

        await experimental.add_interceptor(interceptor)

        assert interceptor in experimental._interceptors
        inner.add_interceptor.assert_awaited_once_with(interceptor)


# ---------------------------------------------------------------------------
# A2AExperimentalClient — broadcast / groupchat operations
# ---------------------------------------------------------------------------


class TestA2AExperimentalClientOperations:
    @pytest.mark.asyncio
    async def test_broadcast_message_sends_send_message_rpc(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(), _rpc_response())
        )

        responses = await experimental.broadcast_message(
            _user_request("ping"), recipients=["a", "b"]
        )

        assert len(responses) == 2
        assert all(isinstance(r, SendMessageResponse) for r in responses)
        assert responses[0].message.parts[0].text == "Hello"
        topic, msg = mock_transport.gather_stream.call_args.args
        assert topic == "test_topic"
        rpc = json.loads(msg.payload)
        assert rpc["method"] == "SendMessage"
        assert rpc["params"]["message"]["parts"][0]["text"] == "ping"

    @pytest.mark.asyncio
    async def test_broadcast_message_skips_intermediate_status_updates(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_status_result(), type_="A2AStatusUpdate"),
                _rpc_response(_message_result("final")),
            )
        )

        responses = await experimental.broadcast_message(
            _user_request(), recipients=["a"]
        )

        assert len(responses) == 1
        assert responses[0].message.parts[0].text == "final"

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_yields_all_events(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(
                    {"task": {"id": "t", "contextId": "c"}}, type_="A2AStatusUpdate"
                ),
                _rpc_response(
                    _status_result("TASK_STATE_WORKING"), type_="A2AStatusUpdate"
                ),
                _rpc_response(_status_result("TASK_STATE_COMPLETED", "done")),
            )
        )

        events = [
            e
            async for e in experimental.broadcast_message_streaming(
                _user_request(), recipients=["a"]
            )
        ]

        assert [e.WhichOneof("payload") for e in events] == [
            "task",
            "status_update",
            "status_update",
        ]
        # Streaming broadcast must request the streaming RPC on the server
        _, msg = mock_transport.gather_stream.call_args.args
        assert json.loads(msg.payload)["method"] == "SendStreamingMessage"

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_stops_after_expected_finals(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_message_result("one")),
                _rpc_response(_message_result("two")),
                _rpc_response(_message_result("three")),
            )
        )

        events = [
            e
            async for e in experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b"]
            )
        ]

        assert len(events) == 2

    @pytest.mark.asyncio
    async def test_broadcast_forbidden_response_is_a_failure(self) -> None:
        """A refusal is not an answer: it must not count as a success."""
        experimental, mock_transport, _ = _make_experimental()
        forbidden = MagicMock()
        forbidden.payload = json.dumps({"error": "forbidden"}).encode("utf-8")
        forbidden.status_code = 403
        forbidden.type = "A2AResponse"
        forbidden.headers = {SENDER_HEADER: "b"}
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_message_result("ok"), sender="a"), forbidden
            )
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError) as exc_info:
                await experimental.broadcast_message(
                    _user_request(), recipients=["a", "b"]
                )

        err = exc_info.value
        assert [r.message.parts[0].text for r in err.responses] == ["ok"]
        assert len(err.failures) == 1 and err.failures[0].sender == "b"
        assert isinstance(err.failures[0].error, A2AClientError)
        assert "forbidden" in str(err.failures[0].error).lower()
        assert any(
            "Broadcast recipient 'b' refused access" in str(c)
            for c in log.warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_broadcast_streaming_forbidden_by_status_code_is_a_failure(
        self,
    ) -> None:
        """A bare 403 (no ``"forbidden"`` body) is a refusal too."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(_message_result(), status_code=403))
        )

        events, error = await _drain(
            experimental.broadcast_message_streaming(_user_request(), recipients=["a"])
        )

        assert events == []
        assert error is not None
        assert isinstance(error.errors[0], A2AClientError)

    def test_groupchat_parsing_keeps_forbidden_as_message(self) -> None:
        """Group chat still uses the synthetic message (unchanged)."""
        smr = A2AExperimentalClient._parse_payload(
            {"error": "forbidden"}, 403, SendMessageResponse()
        )
        assert isinstance(smr, SendMessageResponse)
        assert "Forbidden" in smr.message.parts[0].text

    # -- error replies: count as finals (no hang) AND surface as an error ----

    @pytest.mark.asyncio
    async def test_broadcast_message_error_reply_raises_without_hanging(self) -> None:
        """An error reply must not make the call wait for the transport timeout
        (the transport here never ends on its own) and must not be silent."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(_message_result("ok")),
                _rpc_response(error=_RPC_ERROR),
            )
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await asyncio.wait_for(
                experimental.broadcast_message(_user_request(), recipients=["a", "b"]),
                timeout=_HANG_GUARD_S,
            )

        err = exc_info.value
        assert err.received == 2
        assert len(err.errors) == 1 and isinstance(err.errors[0], InternalError)
        # The healthy recipient's reply is not lost.
        assert [r.message.parts[0].text for r in err.responses] == ["ok"]
        assert "boom" in str(err)

    @pytest.mark.asyncio
    async def test_broadcast_message_all_error_replies_raise(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(error=_RPC_ERROR), _rpc_response(error=_RPC_ERROR)
            )
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await asyncio.wait_for(
                experimental.broadcast_message(_user_request(), recipients=["a", "b"]),
                timeout=_HANG_GUARD_S,
            )

        assert len(exc_info.value.errors) == 2
        assert exc_info.value.responses == []

    @pytest.mark.asyncio
    async def test_broadcast_message_undecodable_reply_raises_and_logs_decoding(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()
        garbage = MagicMock()
        garbage.payload = b"not json"
        garbage.status_code = 200
        garbage.type = "A2AResponse"
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(_rpc_response(_message_result("ok")), garbage)
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError) as exc_info:
                await asyncio.wait_for(
                    experimental.broadcast_message(
                        _user_request(), recipients=["a", "b"]
                    ),
                    timeout=_HANG_GUARD_S,
                )

        assert isinstance(exc_info.value.errors[0], json.JSONDecodeError)
        assert len(exc_info.value.responses) == 1
        # A genuine decode failure keeps the "decoding" heading.
        assert any(
            "Error decoding JSON response" in str(c) for c in log.error.call_args_list
        )

    @pytest.mark.asyncio
    async def test_broadcast_message_rpc_error_is_not_logged_as_decoding(self) -> None:
        """A well-formed JSON-RPC error is the recipient's failure, not a
        decoding problem, so it must not be logged as one."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(_rpc_response(error=_RPC_ERROR))
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError):
                await asyncio.wait_for(
                    experimental.broadcast_message(_user_request(), recipients=["a"]),
                    timeout=_HANG_GUARD_S,
                )

        assert not any("Error decoding" in str(c) for c in log.error.call_args_list)
        assert log.warning.called

    @pytest.mark.asyncio
    async def test_broadcast_message_non_object_payload_is_a_decode_error(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        weird = MagicMock()
        weird.payload = b"[1, 2]"
        weird.status_code = 200
        weird.type = "A2AResponse"
        mock_transport.gather_stream = MagicMock(side_effect=_hanging_gen(weird))

        with pytest.raises(A2ABroadcastError) as exc_info:
            await asyncio.wait_for(
                experimental.broadcast_message(_user_request(), recipients=["a"]),
                timeout=_HANG_GUARD_S,
            )

        assert isinstance(exc_info.value.errors[0], ValueError)

    @pytest.mark.asyncio
    async def test_broadcast_message_status_updates_do_not_count_as_finals(
        self,
    ) -> None:
        """Intermediates must not end the call early, even with errors around."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(_status_result(), type_="A2AStatusUpdate"),
                _rpc_response(error=_RPC_ERROR),
                _rpc_response(_status_result(), type_="A2AStatusUpdate"),
                _rpc_response(_message_result("late")),
            )
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await asyncio.wait_for(
                experimental.broadcast_message(_user_request(), recipients=["a", "b"]),
                timeout=_HANG_GUARD_S,
            )

        assert exc_info.value.received == 2
        assert [r.message.parts[0].text for r in exc_info.value.responses] == ["late"]

    @pytest.mark.asyncio
    async def test_broadcast_message_without_errors_does_not_raise(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(_message_result("one")),
                _rpc_response(_message_result("two")),
            )
        )

        responses = await asyncio.wait_for(
            experimental.broadcast_message(_user_request(), recipients=["a", "b"]),
            timeout=_HANG_GUARD_S,
        )

        assert [r.message.parts[0].text for r in responses] == ["one", "two"]

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_error_reply_raises_after_events(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(_message_result("ok")),
                _rpc_response(error=_RPC_ERROR),
            )
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a", "b"]
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        # Every successful event reaches the caller before the error.
        assert [get_stream_response_text(e) for e in events] == ["ok"]
        assert error is not None and error.received == 2
        assert isinstance(error.errors[0], InternalError)

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_error_reply_first(self) -> None:
        """The failing recipient may reply before the healthy one."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(error=_RPC_ERROR),
                _rpc_response(_message_result("ok")),
            )
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a", "b"]
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        assert [get_stream_response_text(e) for e in events] == ["ok"]
        assert error is not None and len(error.errors) == 1

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_all_error_replies_raise(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(error=_RPC_ERROR), _rpc_response(error=_RPC_ERROR)
            )
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a", "b"]
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        assert events == []
        assert error is not None and len(error.errors) == 2

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_status_updates_not_counted(self) -> None:
        """Intermediates are yielded but never end the stream, even after an error."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(
                    _status_result("TASK_STATE_WORKING", "w1"), type_="A2AStatusUpdate"
                ),
                _rpc_response(error=_RPC_ERROR),
                _rpc_response(
                    _status_result("TASK_STATE_WORKING", "w2"), type_="A2AStatusUpdate"
                ),
                _rpc_response(_message_result("late")),
            )
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a", "b"]
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        assert [get_stream_response_text(e) for e in events] == ["w1", "w2", "late"]
        assert error is not None and error.received == 2

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_honors_message_limit_with_errors(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(error=_RPC_ERROR),
                _rpc_response(_message_result("one")),
                _rpc_response(_message_result("two")),
            )
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a", "b", "c"], message_limit=2
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        assert [get_stream_response_text(e) for e in events] == ["one"]
        assert error is not None and error.received == 2

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_malformed_intermediate_is_skipped(
        self,
    ) -> None:
        """Only *final* failures fail the call; a bad status update is logged."""
        experimental, mock_transport, _ = _make_experimental()
        garbage = MagicMock()
        garbage.payload = b"not json"
        garbage.status_code = 200
        garbage.type = "A2AStatusUpdate"
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(garbage, _rpc_response(_message_result("ok")))
        )

        events, error = await asyncio.wait_for(
            _drain(
                experimental.broadcast_message_streaming(
                    _user_request(), recipients=["a"]
                )
            ),
            timeout=_HANG_GUARD_S,
        )

        assert [get_stream_response_text(e) for e in events] == ["ok"]
        assert error is None

    # -- missing replies, timeouts, sender identity ------------------------

    @pytest.mark.asyncio
    async def test_broadcast_message_stream_ending_early_raises(self) -> None:
        """NATS ends the stream at its timeout without raising: a missing
        reply must not be reported as success."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(_message_result("ok"), sender="a"))
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await experimental.broadcast_message(_user_request(), recipients=["a", "b"])

        err = exc_info.value
        assert not isinstance(err, TimeoutError)
        assert err.timed_out is False
        assert (err.expected, err.received, err.missing) == (2, 1, 1)
        assert err.missing_recipients == ["b"]
        assert err.failures == []
        assert [r.message.parts[0].text for r in err.responses] == ["ok"]
        assert err.succeeded == ["a"]
        assert "1 of 2" in str(err) and "1 missing [b]" in str(err)

    @pytest.mark.asyncio
    async def test_broadcast_message_empty_stream_raises(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(side_effect=_async_gen())

        with pytest.raises(A2ABroadcastError) as exc_info:
            await experimental.broadcast_message(_user_request(), recipients=["a", "b"])

        assert exc_info.value.missing_recipients == ["a", "b"]
        assert exc_info.value.responses == []

    @pytest.mark.asyncio
    async def test_broadcast_message_transport_timeout_keeps_partial_responses(
        self,
    ) -> None:
        """SLIM raises at its timeout: the replies already received survive."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_timeout_gen(
                _rpc_response(_message_result("ok"), sender="a"),
                _rpc_response(error=_RPC_ERROR, sender="b"),
            )
        )

        with pytest.raises(A2ABroadcastTimeoutError) as exc_info:
            await experimental.broadcast_message(
                _user_request(), recipients=["a", "b", "c"]
            )

        err = exc_info.value
        # Existing ``except TimeoutError`` handlers still catch it.
        assert isinstance(err, TimeoutError)
        assert isinstance(err, asyncio.TimeoutError)
        assert isinstance(err, A2ABroadcastError)
        assert err.timed_out is True
        assert isinstance(err.__cause__, asyncio.TimeoutError)
        assert [r.message.parts[0].text for r in err.responses] == ["ok"]
        assert [f.sender for f in err.failures] == ["b"]
        assert err.missing_recipients == ["c"]
        assert str(err).startswith("Broadcast timed out")

    @pytest.mark.asyncio
    async def test_broadcast_message_failure_names_its_sender(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response(_message_result("ok"), sender="a"),
                _rpc_response(error=_RPC_ERROR, sender="b"),
            )
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await asyncio.wait_for(
                experimental.broadcast_message(_user_request(), recipients=["a", "b"]),
                timeout=_HANG_GUARD_S,
            )

        err = exc_info.value
        assert len(err.failures) == 1
        assert err.failures[0].sender == "b"
        assert isinstance(err.failures[0].error, InternalError)
        assert err.errors == [err.failures[0].error]
        assert err.succeeded == ["a"]
        assert err.missing_recipients == []
        assert "b: InternalError" in str(err)

    @pytest.mark.asyncio
    async def test_broadcast_message_duplicate_final_does_not_count_twice(self) -> None:
        """One recipient answering twice must not stand in for a silent one."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_message_result("first"), sender="a"),
                _rpc_response(_message_result("again"), sender="a"),
            )
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError) as exc_info:
                await experimental.broadcast_message(
                    _user_request(), recipients=["a", "b"]
                )

        err = exc_info.value
        assert err.received == 1
        assert [r.message.parts[0].text for r in err.responses] == ["first"]
        assert err.missing_recipients == ["b"]
        assert any("duplicate" in str(c) for c in log.warning.call_args_list)

    @pytest.mark.asyncio
    async def test_broadcast_message_anonymous_replies_are_counted(self) -> None:
        """Replies without a sender header (older servers) still count, but
        the missing recipients can no longer be named."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_message_result("one")),
                _rpc_response(_message_result("two")),
            )
        )

        responses = await experimental.broadcast_message(
            _user_request(), recipients=["a", "b"]
        )
        assert len(responses) == 2

        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(_message_result("one")))
        )
        with pytest.raises(A2ABroadcastError) as exc_info:
            await experimental.broadcast_message(_user_request(), recipients=["a", "b"])
        assert exc_info.value.missing == 1
        assert exc_info.value.missing_recipients == []

    @pytest.mark.asyncio
    async def test_broadcast_message_unknown_sender_does_not_guess_missing(
        self,
    ) -> None:
        """A sender id that is not one of the recipient names (e.g. a SLIM
        topic that differs from its app name) must not make the client
        blame a recipient that may well have answered."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(_message_result(), sender="x"))
        )

        with pytest.raises(A2ABroadcastError) as exc_info:
            await experimental.broadcast_message(_user_request(), recipients=["a", "b"])

        assert exc_info.value.missing == 1
        assert exc_info.value.missing_recipients == []

    @pytest.mark.asyncio
    async def test_broadcast_message_bad_result_is_logged_as_decoding(self) -> None:
        """A reply with a ``result`` that does not fit the schema is a decode
        problem, not the recipient replying with an error."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(
                _rpc_response({"message": {"parts": "not a list"}}, sender="a")
            )
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError):
                await asyncio.wait_for(
                    experimental.broadcast_message(_user_request(), recipients=["a"]),
                    timeout=_HANG_GUARD_S,
                )

        assert any(
            "Error decoding JSON response from 'a'" in str(c)
            for c in log.error.call_args_list
        )
        assert not any(
            "replied with an error" in str(c) for c in log.warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_broadcast_message_error_reply_log_names_sender(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_hanging_gen(_rpc_response(error=_RPC_ERROR, sender="b"))
        )

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.experimental_patterns.logger"
        ) as log:
            with pytest.raises(A2ABroadcastError):
                await asyncio.wait_for(
                    experimental.broadcast_message(_user_request(), recipients=["b"]),
                    timeout=_HANG_GUARD_S,
                )

        assert any(
            "Broadcast recipient 'b' replied with an error" in str(c)
            for c in log.warning.call_args_list
        )

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_stream_ending_early_raises(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(
                    _status_result("TASK_STATE_WORKING", "w"),
                    type_="A2AStatusUpdate",
                    sender="b",
                ),
                _rpc_response(_message_result("ok"), sender="a"),
            )
        )

        events, error = await _drain(
            experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b"]
            )
        )

        # Events are yielded first; a status update from "b" is not a final.
        assert [get_stream_response_text(e) for e in events] == ["w", "ok"]
        assert error is not None and not isinstance(error, TimeoutError)
        assert error.missing_recipients == ["b"]
        assert error.succeeded == ["a"]
        assert error.responses == []

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_transport_timeout_raises(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_timeout_gen(_rpc_response(_message_result("ok"), sender="a"))
        )

        events, error = await _drain(
            experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b"]
            )
        )

        assert [get_stream_response_text(e) for e in events] == ["ok"]
        assert isinstance(error, A2ABroadcastTimeoutError)
        assert isinstance(error, TimeoutError)
        assert error.missing_recipients == ["b"]

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_partial_limit_names_no_one(self) -> None:
        """With message_limit below len(recipients) any subset may answer, so
        a shortfall cannot be pinned on particular recipients."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response(_message_result("ok"), sender="a"))
        )

        _, error = await _drain(
            experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b", "c"], message_limit=2
            )
        )

        assert error is not None and error.missing == 1
        assert error.missing_recipients == []

    @pytest.mark.asyncio
    async def test_broadcast_message_transport_failure_propagates(self) -> None:
        """A broadcast that never went out must not look like "no replies"."""
        experimental, mock_transport, _ = _make_experimental()
        closed = asyncio.Event()

        async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            try:
                raise RuntimeError("NATS client is not connected")
                yield  # pragma: no cover - makes this an async generator
            finally:
                closed.set()

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        with pytest.raises(RuntimeError, match="not connected"):
            await experimental.broadcast_message(_user_request(), recipients=["a"])
        assert closed.is_set()

    @pytest.mark.asyncio
    async def test_broadcast_message_missing_recipients_error_propagates(self) -> None:
        experimental, mock_transport, _ = _make_experimental()

        async def _gen(
            *_args: Any, recipients: list[str] | None = None, **_kwargs: Any
        ) -> AsyncIterator[Any]:
            if not recipients:
                raise ValueError("recipients list must be provided")
            yield  # pragma: no cover

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        with pytest.raises(ValueError, match="recipients"):
            await experimental.broadcast_message(_user_request(), recipients=[])

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_transport_failure_propagates(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()

        async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            raise RuntimeError("publish failed")
            yield  # pragma: no cover - makes this an async generator

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        with pytest.raises(RuntimeError, match="publish failed"):
            async for _ in experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b"]
            ):
                pass

    @pytest.mark.asyncio
    async def test_broadcast_message_transport_failure_after_reply_keeps_it(
        self,
    ) -> None:
        """A session that dies mid-broadcast must not lose the replies so far."""
        experimental, mock_transport, _ = _make_experimental()
        cause = RuntimeError("session closed")

        async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            yield _rpc_response(_message_result("ok"), sender="a")
            raise cause

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        with pytest.raises(A2ABroadcastError) as excinfo:
            await experimental.broadcast_message(_user_request(), recipients=["a", "b"])
        error = excinfo.value
        assert not isinstance(error, TimeoutError)
        assert error.transport_error is cause
        assert error.__cause__ is cause
        assert error.succeeded == ["a"]
        assert len(error.responses) == 1
        assert error.missing_recipients == ["b"]
        assert "transport error: RuntimeError: session closed" in str(error)

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_transport_failure_after_reply(
        self,
    ) -> None:
        experimental, mock_transport, _ = _make_experimental()
        cause = RuntimeError("session closed")

        async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            yield _rpc_response(_message_result("ok"), sender="a")
            raise cause

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        events, error = await _drain(
            experimental.broadcast_message_streaming(
                _user_request(), recipients=["a", "b"]
            )
        )
        assert [get_stream_response_text(e) for e in events] == ["ok"]
        assert error is not None and error.transport_error is cause
        assert error.succeeded == ["a"]
        assert error.responses == []
        assert error.missing_recipients == ["b"]

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_early_stop_does_not_raise(self) -> None:
        """A caller that stops iterating gets no error for replies it did
        not wait for, and the transport stream is closed."""
        experimental, mock_transport, _ = _make_experimental()
        closed = asyncio.Event()

        async def _gen(*_args: Any, **_kwargs: Any) -> AsyncIterator[Any]:
            try:
                yield _rpc_response(_message_result("one"), sender="a")
                await asyncio.Event().wait()
            finally:
                closed.set()

        mock_transport.gather_stream = MagicMock(side_effect=_gen)

        stream = experimental.broadcast_message_streaming(
            _user_request(), recipients=["a", "b"]
        )
        async for _event in stream:
            break
        assert isinstance(stream, AsyncGenerator)
        await stream.aclose()

        assert closed.is_set()

    @pytest.mark.asyncio
    async def test_start_groupchat(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.start_conversation = AsyncMock(
            return_value=[_rpc_response(), _rpc_response()]
        )

        responses = await experimental.start_groupchat(
            _user_request(),
            group_channel="zoo",
            participants=["a", "b"],
            end_message="DELIVERED",
        )

        assert len(responses) == 2
        kwargs = mock_transport.start_conversation.call_args.kwargs
        assert kwargs["group_channel"] == "zoo"
        assert kwargs["participants"] == ["a", "b"]
        assert json.loads(kwargs["init_message"].payload)["method"] == "SendMessage"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            NotImplementedError(),
            RuntimeError("session closed"),
            ValueError("participants list must be provided"),
        ],
    )
    async def test_start_groupchat_transport_failure_propagates(
        self, error: Exception
    ) -> None:
        """A failed group chat must not look like one where nobody spoke."""
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.start_conversation = AsyncMock(side_effect=error)

        with pytest.raises(type(error)):
            await experimental.start_groupchat(
                _user_request(), group_channel="zoo", participants=["a"]
            )

    @pytest.mark.asyncio
    async def test_start_groupchat_skips_unreadable_messages(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        not_json = _rpc_response()
        not_json.payload = b"not json"
        mock_transport.start_conversation = AsyncMock(
            return_value=[
                _rpc_response(_message_result("ok")),
                _rpc_response(error={"code": -32603, "message": "agent crashed"}),
                not_json,
            ]
        )

        responses = await experimental.start_groupchat(
            _user_request(), group_channel="zoo", participants=["a", "b"]
        )

        assert len(responses) == 1
        assert responses[0].message.parts[0].text == "ok"

    @pytest.mark.asyncio
    async def test_start_streaming_groupchat(self) -> None:
        experimental, mock_transport, _ = _make_experimental()
        mock_transport.start_streaming_conversation = MagicMock(
            side_effect=_async_gen(_rpc_response(), _rpc_response())
        )

        events = [
            e
            async for e in experimental.start_streaming_groupchat(
                _user_request(),
                group_channel="zoo",
                participants=["a", "b"],
            )
        ]

        assert len(events) == 2
        assert all(isinstance(e, StreamResponse) for e in events)
        assert get_stream_response_text(events[0]) == "Hello"


# ---------------------------------------------------------------------------
# A2AClientFactory tests
# ---------------------------------------------------------------------------


class TestA2AClientFactory:
    def test_constructor_default_config(self) -> None:
        factory = A2AClientFactory()
        assert factory._config.supported_protocol_bindings == ["JSONRPC"]

    def test_constructor_with_config(self) -> None:
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        factory = A2AClientFactory(config)
        assert factory._config is config
        assert "slimpatterns" in factory._config.supported_protocol_bindings

    # -- Negotiation tests --------------------------------------------------

    def test_negotiate_server_preference(self) -> None:
        """Default negotiation follows the card's interface order."""
        factory = A2AClientFactory(ClientConfig(slim_transport=_make_mock_transport()))

        card = _make_agent_card(
            [
                _iface("slimpatterns", "slim://my_agent"),
                _iface("JSONRPC", "http://localhost:8080"),
            ]
        )
        label, url = factory._negotiate(card)
        assert label == "slimpatterns"
        assert url == "slim://my_agent"

    def test_negotiate_fallback_to_jsonrpc(self) -> None:
        """Unsupported transports are skipped; the first supported one wins."""
        factory = A2AClientFactory(ClientConfig())  # only JSONRPC

        card = _make_agent_card(
            [
                _iface("grpc", "grpc://my_agent"),
                _iface("JSONRPC", "http://localhost:8080"),
            ]
        )
        label, url = factory._negotiate(card)
        assert label == "JSONRPC"
        assert url == "http://localhost:8080"

    def test_negotiate_no_match_raises(self) -> None:
        factory = A2AClientFactory(
            ClientConfig(supported_protocol_bindings=["custom_only"])
        )

        card = _make_agent_card([_iface("grpc", "grpc://agent")])
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    def test_negotiate_empty_card_raises(self) -> None:
        factory = A2AClientFactory(ClientConfig())
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(_make_agent_card([]))

    def test_negotiate_client_preference(self) -> None:
        """With use_client_preference, the client's order wins."""
        config = ClientConfig(
            slim_transport=_make_mock_transport(),
            nats_transport=_make_mock_transport("NATS"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        # Server lists natspatterns first, but the client's list has JSONRPC first
        card = _make_agent_card(
            [
                _iface("natspatterns", "nats://my_agent"),
                _iface("JSONRPC", "http://localhost:8080"),
            ]
        )
        label, url = factory._negotiate(card)
        assert label == "JSONRPC"
        assert url == "http://localhost:8080"

    # -- _select_card -------------------------------------------------------

    def test_select_card_keeps_only_chosen_interface(self) -> None:
        card = _make_agent_card(
            [
                _iface("slimpatterns", "slim://a"),
                _iface("jsonrpc", "http://localhost:8080"),
            ]
        )
        selected = A2AClientFactory._select_card(
            card, "jsonrpc", "http://localhost:8080"
        )

        assert [(i.protocol_binding, i.url) for i in selected.supported_interfaces] == [
            ("JSONRPC", "http://localhost:8080")
        ]
        # the caller's card is untouched
        assert len(card.supported_interfaces) == 2
        assert card.supported_interfaces[1].protocol_binding == "jsonrpc"

    def test_select_card_url_override(self) -> None:
        card = _make_agent_card([_iface("slimrpc", "slim://host:46357/org/ns/agent")])
        selected = A2AClientFactory._select_card(
            card, "slimrpc", "slim://host:46357/org/ns/agent", url="org/ns/agent"
        )
        assert selected.supported_interfaces[0].url == "org/ns/agent"
        assert selected.supported_interfaces[0].protocol_binding == "slimrpc"

    # -- create() async path tests ------------------------------------------

    @pytest.mark.asyncio
    async def test_create_with_eager_slim_transport(self) -> None:
        mock_transport = _make_mock_transport("SLIM")
        factory = A2AClientFactory(ClientConfig(slim_transport=mock_transport))

        card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])
        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert isinstance(result, Client)
        assert result.agent_card is card
        assert result.transport is mock_transport
        assert result.topic == "my_agent"
        mock_transport.setup.assert_awaited()

    @pytest.mark.asyncio
    async def test_create_with_eager_nats_transport(self) -> None:
        mock_transport = _make_mock_transport("NATS")
        factory = A2AClientFactory(ClientConfig(nats_transport=mock_transport))

        card = _make_agent_card([_iface("natspatterns", "nats://my_agent")])
        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport is mock_transport
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_jsonrpc_returns_upstream_client(self) -> None:
        """JSONRPC returns the upstream Client, not A2AExperimentalClient."""
        factory = A2AClientFactory(ClientConfig())

        result = await factory.create(_make_agent_card())  # defaults to JSONRPC

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)

    @pytest.mark.asyncio
    async def test_create_accepts_lowercase_jsonrpc_binding(self) -> None:
        """Cards written with the SDK's lowercase constant still negotiate."""
        factory = A2AClientFactory(ClientConfig())

        result = await factory.create(
            _make_agent_card([_iface("jsonrpc", "http://localhost:8080")])
        )

        assert isinstance(result, Client)

    @pytest.mark.asyncio
    async def test_create_does_not_mutate_card(self) -> None:
        factory = A2AClientFactory(ClientConfig())
        card = _make_agent_card([_iface("jsonrpc", "http://localhost:8080")])
        before = AgentCard()
        before.CopyFrom(card)

        await factory.create(card)

        assert card == before

    @pytest.mark.asyncio
    async def test_create_deferred_slim_missing_config_raises(self) -> None:
        """slimpatterns negotiated but neither config nor transport set → error."""
        config = ClientConfig(supported_protocol_bindings=["slimpatterns", "JSONRPC"])
        factory = A2AClientFactory(config)

        card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])
        with pytest.raises(ValueError, match="neither slim_transport nor slim_config"):
            await factory.create(card)

    @pytest.mark.asyncio
    async def test_create_consumers_are_deprecated_and_ignored(self) -> None:
        """a2a-sdk 1.x removed client consumers; passing them warns."""
        factory = A2AClientFactory(ClientConfig())

        with pytest.warns(DeprecationWarning, match="consumers"):
            result = await factory.create(_make_agent_card(), consumers=[AsyncMock()])

        assert isinstance(result, Client)

    # -- connect() classmethod test -----------------------------------------

    @pytest.mark.asyncio
    async def test_connect_with_card(self) -> None:
        """connect() with an AgentCard should skip HTTP resolution."""
        result = await A2AClientFactory.connect(
            _make_agent_card(), config=ClientConfig()
        )
        assert isinstance(result, Client)

    @pytest.mark.asyncio
    async def test_connect_backfills_jsonrpc_interface_for_bare_card(self) -> None:
        """A resolved card with no interfaces is assumed to be JSONRPC at the base URL."""
        resolved = AgentCard(name="bare", version="1")

        with patch(
            "agntcy_app_sdk.semantic.a2a.client.factory.A2ACardResolver"
        ) as resolver_cls:
            resolver_cls.return_value.get_agent_card = AsyncMock(return_value=resolved)
            client = await A2AClientFactory.connect(
                "http://agent.example:9000", config=ClientConfig()
            )

        assert isinstance(client, Client)
        assert [(i.protocol_binding, i.url) for i in resolved.supported_interfaces] == [
            ("JSONRPC", "http://agent.example:9000")
        ]


# ---------------------------------------------------------------------------
# Multi-transport negotiation tests
# ---------------------------------------------------------------------------


def _make_multi_transport_factory() -> tuple[A2AClientFactory, ClientConfig]:
    """Build an A2AClientFactory whose ClientConfig supports all transports."""
    config = ClientConfig(
        slimrpc_channel_factory=MagicMock(),
        slim_transport=_make_mock_transport("SLIM"),
        nats_transport=_make_mock_transport("NATS"),
    )
    return A2AClientFactory(config), config


class TestMultiTransportNegotiation:
    """A ClientConfig with slimrpc, slimpatterns and natspatterns configured
    simultaneously, negotiated against various server cards.  Server
    preference is the order of ``supported_interfaces``.
    """

    def test_bindings_contain_all(self) -> None:
        _factory, config = _make_multi_transport_factory()
        assert config.supported_protocol_bindings == [
            "JSONRPC",
            "slimpatterns",
            "natspatterns",
            "slimrpc",
        ]

    @pytest.mark.parametrize(
        ("binding", "url"),
        [
            ("slimrpc", "default/default/Hello_World_Agent_1.0.0"),
            ("slimpatterns", "slim://my_agent_topic"),
            ("natspatterns", "nats://my_agent_topic"),
            ("JSONRPC", "http://localhost:9999"),
        ],
    )
    def test_server_preference_per_transport(self, binding: str, url: str) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface(binding, url)])

        assert factory._negotiate(card) == (binding, url)

    def test_first_listed_interface_wins(self) -> None:
        """Even though the client supports all of them, the card's first entry wins."""
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [
                _iface("slimrpc", "default/default/agent"),
                _iface("slimpatterns", "slim://agent_topic"),
                _iface("natspatterns", "nats://agent_topic"),
                _iface("JSONRPC", "http://localhost:9999"),
            ]
        )

        assert factory._negotiate(card) == ("slimrpc", "default/default/agent")

    def test_unsupported_first_falls_back_to_next_supported(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [
                _iface("grpc", "grpc://agent"),
                _iface("natspatterns", "nats://agent_topic"),
                _iface("JSONRPC", "http://localhost:9999"),
            ]
        )

        assert factory._negotiate(card) == ("natspatterns", "nats://agent_topic")

    def test_client_preference_overrides_server(self) -> None:
        config = ClientConfig(
            slimrpc_channel_factory=MagicMock(),
            slim_transport=_make_mock_transport("SLIM"),
            nats_transport=_make_mock_transport("NATS"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        # Client order: JSONRPC, slimpatterns, natspatterns, slimrpc
        card = _make_agent_card(
            [
                _iface("slimrpc", "default/default/agent"),
                _iface("JSONRPC", "http://localhost:9999"),
            ]
        )

        assert factory._negotiate(card) == ("JSONRPC", "http://localhost:9999")

    def test_no_match_raises(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [_iface("grpc", "grpc://agent"), _iface("websocket", "ws://agent")]
        )
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    def test_same_binding_prefers_protocol_version_1_0(self) -> None:
        """Like upstream: a 1.0 interface wins over an earlier 0.3 one."""
        factory = A2AClientFactory(ClientConfig())
        legacy = _iface("JSONRPC", "http://old:9999")
        legacy.protocol_version = "0.3"
        current = _iface("JSONRPC", "http://new:9999")
        current.protocol_version = "1.0"

        assert factory._negotiate(_make_agent_card([legacy, current])) == (
            "JSONRPC",
            "http://new:9999",
        )

    def test_protocol_version_does_not_override_binding_preference(self) -> None:
        """Version only breaks ties within the negotiated binding."""
        factory, _ = _make_multi_transport_factory()
        rpc = _iface("slimrpc", "default/default/agent")
        http = _iface("JSONRPC", "http://localhost:9999")
        http.protocol_version = "1.0"

        assert factory._negotiate(_make_agent_card([rpc, http])) == (
            "slimrpc",
            "default/default/agent",
        )

    # -- create() dispatches to correct path --------------------------------

    @pytest.mark.asyncio
    async def test_create_dispatches_slimrpc(self) -> None:
        """slimrpc goes through the upstream sync path → upstream Client."""
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimrpc", "default/default/agent")])

        result = await factory.create(card)

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)

    @pytest.mark.asyncio
    async def test_create_slimrpc_strips_slim_scheme_from_url(self) -> None:
        """The channel factory receives a bare ``org/ns/name`` identity."""
        factory, config = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimrpc", "slim://host:46357/org/ns/agent")])

        await factory.create(card)

        assert isinstance(config.slimrpc_channel_factory, MagicMock)
        config.slimrpc_channel_factory.assert_called_once_with("org/ns/agent")

    @pytest.mark.asyncio
    async def test_create_dispatches_slimpatterns(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_natspatterns(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("natspatterns", "nats://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_jsonrpc_after_unsupported(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [
                _iface("grpc", "grpc://agent"),
                _iface("JSONRPC", "http://localhost:9999"),
            ]
        )

        result = await factory.create(card)

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)


# =========================================================================
# Transport alias resolution in negotiation
# =========================================================================


class TestTransportAliasNegotiation:
    """Aliases ("slim" -> "slimpatterns", "nats" -> "natspatterns") are
    resolved during negotiation and dispatch, so cards using alias names
    still produce valid clients.
    """

    @pytest.mark.parametrize(
        ("alias", "url"),
        [
            ("slim", "slim://my_topic"),
            ("nats", "nats://my_topic"),
            ("slim-extended", "slim://my_topic"),
            ("SLIM", "slim://my_topic"),
        ],
    )
    def test_negotiate_alias_binding(self, alias: str, url: str) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface(alias, url)])

        assert factory._negotiate(card) == (alias, url)

    def test_negotiate_alias_after_unsupported(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [_iface("grpc", "grpc://agent"), _iface("slim", "slim://my_topic")]
        )

        assert factory._negotiate(card) == ("slim", "slim://my_topic")

    @pytest.mark.asyncio
    async def test_create_slim_alias_dispatches_to_slimpatterns(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slim", "slim://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_nats_alias_dispatches_to_natspatterns(self) -> None:
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("nats", "nats://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    def test_client_preference_resolves_aliases(self) -> None:
        config = ClientConfig(
            slim_transport=_make_mock_transport("SLIM"),
            use_client_preference=True,
        )
        factory = A2AClientFactory(config)

        # Client supports "slimpatterns"; server offers "slim" (alias).
        card = _make_agent_card([_iface("slim", "slim://my_topic")])

        assert factory._negotiate(card) == ("slim", "slim://my_topic")


# ---------------------------------------------------------------------------
# Tests for _build_slimrpc_if_needed() — trailing-slash connection isolation
# ---------------------------------------------------------------------------


class TestBuildSlimrpcIfNeeded:
    """``_build_slimrpc_if_needed()`` opens a dedicated SLIM connection via the
    trailing-slash endpoint trick, matching the server-side pattern in
    ``A2ASRPCServerHandler``.
    """

    @pytest.mark.asyncio
    async def test_slimrpc_uses_trailing_slash_endpoint(self) -> None:
        mock_service = MagicMock()

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
                "agntcy_app_sdk.transport.slim.common.get_or_init_slim_service",
                return_value=mock_service,
            ) as mock_get_service,
            patch("slim_bindings.uniffi_set_event_loop", MagicMock()),
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
            mock_service.connect_async = AsyncMock(return_value=99)
            mock_rpc_app = MagicMock()
            mock_rpc_app.subscribe_async = AsyncMock()
            mock_service.create_app_with_secret = MagicMock(return_value=mock_rpc_app)

            await factory._build_slimrpc_if_needed()

            # Should have taken the global service first (no pub/sub App)
            mock_get_service.assert_called_once()

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
    async def test_slimrpc_noop_when_eager_factory_set(self) -> None:
        eager_factory = MagicMock()
        config = ClientConfig(slimrpc_channel_factory=eager_factory)
        factory = A2AClientFactory(config)

        await factory._build_slimrpc_if_needed()

        # Should not have changed the factory
        assert config.slimrpc_channel_factory is eager_factory


# ---------------------------------------------------------------------------
# Interceptors — standard operations (via the upstream BaseClient)
# ---------------------------------------------------------------------------


async def _patterns_client(
    interceptors: list[ClientCallInterceptor] | None = None,
    streaming: bool = False,
) -> tuple[A2AExperimentalClient, MagicMock]:
    mock_transport = _make_mock_transport("SLIM")
    config = ClientConfig(slim_transport=mock_transport, streaming=streaming)
    factory = A2AClientFactory(config)
    card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])
    card.capabilities.streaming = streaming
    client = await factory.create(card, interceptors=interceptors)
    assert isinstance(client, A2AExperimentalClient)
    return client, mock_transport


class TestFactoryInterceptorIntegration:
    @pytest.mark.asyncio
    async def test_send_message_runs_before_and_after_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport = await _patterns_client([interceptor])
        mock_transport.request.return_value = _rpc_response(_message_result("Hello"))

        events = [e async for e in client.send_message(_user_request())]

        assert [get_stream_response_text(e) for e in events] == ["Hello"]
        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert [a.method for a in interceptor.after_calls] == ["send_message"]
        assert interceptor.before_calls[0].agent_card.name == "test-agent"

    @pytest.mark.asyncio
    async def test_streaming_send_message_uses_streaming_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport = await _patterns_client([interceptor], streaming=True)
        mock_transport.request_stream = MagicMock(
            side_effect=_async_gen(
                _rpc_response(_status_result(), type_="A2AStatusUpdate"),
                _rpc_response(_status_result("TASK_STATE_COMPLETED")),
            )
        )

        events = [e async for e in client.send_message(_user_request())]

        assert len(events) == 2
        assert [a.method for a in interceptor.before_calls] == [
            "send_message_streaming"
        ]
        assert len(interceptor.after_calls) == 2

    @pytest.mark.asyncio
    async def test_interceptor_headers_reach_the_wire(self) -> None:
        """service_parameters set in ``before`` become transport message headers."""
        interceptor = _RecordingInterceptor(headers={"Authorization": "Bearer abc"})
        client, mock_transport = await _patterns_client([interceptor])
        mock_transport.request.return_value = _rpc_response()

        _ = [e async for e in client.send_message(_user_request())]

        sent = mock_transport.request.call_args.args[1]
        assert sent.headers["Authorization"] == "Bearer abc"

    @pytest.mark.asyncio
    async def test_get_task_runs_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport = await _patterns_client([interceptor])
        mock_transport.request.return_value = _rpc_response(
            {"id": "t1", "status": {"state": "TASK_STATE_WORKING"}}
        )

        task = await client.get_task(GetTaskRequest(id="t1"))

        assert task.id == "t1"
        assert [a.method for a in interceptor.before_calls] == ["get_task"]
        assert [a.method for a in interceptor.after_calls] == ["get_task"]

    @pytest.mark.asyncio
    async def test_interceptor_chaining_order(self) -> None:
        """``before`` runs in registration order, ``after`` in reverse."""
        order: list[str] = []

        class _Ordered(ClientCallInterceptor):
            def __init__(self, label: str) -> None:
                self._label = label

            async def before(self, args: BeforeArgs) -> None:
                order.append(f"before:{self._label}")

            async def after(self, args: AfterArgs) -> None:
                order.append(f"after:{self._label}")

        client, mock_transport = await _patterns_client([_Ordered("a"), _Ordered("b")])
        mock_transport.request.return_value = _rpc_response()

        _ = [e async for e in client.send_message(_user_request())]

        assert order == ["before:a", "before:b", "after:b", "after:a"]

    @pytest.mark.asyncio
    async def test_no_interceptors_passthrough(self) -> None:
        client, mock_transport = await _patterns_client()
        mock_transport.request.return_value = _rpc_response(_message_result("plain"))

        events = [e async for e in client.send_message(_user_request())]

        assert get_stream_response_text(events[0]) == "plain"


class TestIdentityAuthOverJsonRpc:
    """With identity auth enabled, the HTTP JSON-RPC client sends a token."""

    @staticmethod
    def _identity(
        monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, token: str = "tok"
    ) -> MagicMock:
        if enabled:
            monkeypatch.setenv("IDENTITY_AUTH_ENABLED", "true")
            monkeypatch.setenv("IDENTITY_SERVICE_API_KEY", "unit-test-placeholder")
        else:
            monkeypatch.delenv("IDENTITY_AUTH_ENABLED", raising=False)
        sdk = MagicMock()
        sdk.access_token.return_value = token
        monkeypatch.setattr(
            auth,
            "_fetch_access_token",
            lambda tool_name: sdk.access_token(tool_name=tool_name),
        )
        return sdk

    @staticmethod
    async def _send(
        *,
        interceptors: list[ClientCallInterceptor] | None = None,
        context: ClientCallContext | None = None,
        url: str = "http://localhost:8080",
        card: AgentCard | None = None,
    ) -> httpx.Request:
        """Send one message over JSON-RPC; return the HTTP request that went out.

        Without *card*, the server's card requires the Identity Service scheme.
        """
        if card is None:
            card = _make_agent_card([_iface("JSONRPC", url)])
            declare_identity_auth(card)
        sent: list[httpx.Request] = []

        def reply(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            rpc_id = json.loads(request.content)["id"]
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": rpc_id, "result": _message_result()},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        client = await A2AClientFactory(
            ClientConfig(httpx_client=http, streaming=False)
        ).create(card, interceptors=interceptors)
        _ = [e async for e in client.send_message(_user_request(), context=context)]
        await http.aclose()
        return sent[0]

    @pytest.mark.asyncio
    async def test_bearer_token_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._identity(monkeypatch)

        request = await self._send()

        assert request.headers["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_no_token_to_a_server_that_does_not_ask(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)

        request = await self._send(
            card=_make_agent_card([_iface("JSONRPC", "http://localhost:8080")])
        )

        assert "Authorization" not in request.headers
        sdk.access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token_when_the_scheme_is_defined_but_not_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)
        card = _make_agent_card([_iface("JSONRPC", "http://localhost:8080")])
        declare_identity_auth(card)
        del card.security_requirements[:]

        request = await self._send(card=card)

        assert "Authorization" not in request.headers
        sdk.access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_token_when_auth_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch, enabled=False)

        request = await self._send()

        assert "Authorization" not in request.headers
        sdk.access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_caller_authorization_wins(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)
        interceptor = _RecordingInterceptor(headers={"Authorization": "Bearer mine"})

        request = await self._send(interceptors=[interceptor])

        assert request.headers["Authorization"] == "Bearer mine"
        sdk.access_token.assert_not_called()

    @pytest.mark.asyncio
    async def test_caller_context_is_not_modified(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reused context must not keep a token, or it would go stale."""
        self._identity(monkeypatch)
        context = ClientCallContext(service_parameters={"X-Trace": "1"})

        request = await self._send(context=context)

        assert request.headers["Authorization"] == "Bearer tok"
        assert request.headers["X-Trace"] == "1"
        assert context.service_parameters == {"X-Trace": "1"}

    @pytest.mark.asyncio
    async def test_token_failure_sends_without_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)
        sdk.access_token.side_effect = RuntimeError("identity service down")

        request = await self._send()

        assert "Authorization" not in request.headers

    @staticmethod
    def _cacheable_token(name: str) -> str:
        """An unsigned JWT with a future ``exp``, so the client caches it."""

        def part(data: dict[str, Any]) -> str:
            return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()

        return f"{part({'alg': 'none'})}.{part({'sub': name, 'exp': 4102444800})}."

    @staticmethod
    async def _client_replying(
        statuses: list[int], *, streaming: bool = False
    ) -> tuple[Client, list[httpx.Request], httpx.AsyncClient]:
        """A JSON-RPC client whose server answers with *statuses* in turn."""
        card = _make_agent_card([_iface("JSONRPC", "http://localhost:8080")])
        card.capabilities.streaming = streaming
        declare_identity_auth(card)
        sent: list[httpx.Request] = []

        def reply(request: httpx.Request) -> httpx.Response:
            sent.append(request)
            status = statuses[len(sent) - 1]
            if status != 200:
                return httpx.Response(status, json={"error": "unauthorized"})
            rpc_id = json.loads(request.content)["id"]
            return httpx.Response(
                200,
                json={"jsonrpc": "2.0", "id": rpc_id, "result": _message_result()},
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
        client = await A2AClientFactory(
            ClientConfig(httpx_client=http, streaming=streaming)
        ).create(card)
        return client, sent, http

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_rejected_token_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch, status: int
    ) -> None:
        """After a 401 / 403 the next request fetches a fresh token."""
        sdk = self._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        old, new = self._cacheable_token("old"), self._cacheable_token("new")
        sdk.access_token.side_effect = [old, new]
        client, sent, http = await self._client_replying([status, 200])

        with pytest.raises(A2AClientError) as excinfo:
            _ = [e async for e in client.send_message(_user_request())]
        _ = [e async for e in client.send_message(_user_request())]
        await http.aclose()

        assert isinstance(excinfo.value.__cause__, httpx.HTTPStatusError)
        assert sent[0].headers["Authorization"] == f"Bearer {old}"
        assert sent[1].headers["Authorization"] == f"Bearer {new}"
        assert sdk.access_token.call_count == 2

    @pytest.mark.asyncio
    async def test_rejected_token_is_dropped_when_streaming(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        sdk.access_token.return_value = self._cacheable_token("tok")
        client, _, http = await self._client_replying([401], streaming=True)

        with pytest.raises(A2AClientError):
            _ = [e async for e in client.send_message(_user_request())]
        await http.aclose()

        assert auth._token_cache == {}

    @pytest.mark.asyncio
    async def test_other_errors_keep_the_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sdk = self._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        sdk.access_token.return_value = self._cacheable_token("tok")
        client, _, http = await self._client_replying([500, 200])

        with pytest.raises(A2AClientError):
            _ = [e async for e in client.send_message(_user_request())]
        _ = [e async for e in client.send_message(_user_request())]
        await http.aclose()

        assert sdk.access_token.call_count == 1

    @pytest.mark.asyncio
    async def test_not_wrapped_when_the_card_does_not_ask(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._identity(monkeypatch)
        card = _make_agent_card([_iface("JSONRPC", "http://localhost:8080")])

        client = await A2AClientFactory(ClientConfig()).create(card)

        assert isinstance(client, BaseClient)


class TestIdentityAuthOverSlimRpc:
    """With identity auth enabled, slimrpc calls carry the token as metadata."""

    @staticmethod
    def _channel(*replies: bytes | Exception) -> MagicMock:
        """A SLIM channel whose unary calls answer with *replies* in turn."""
        channel = MagicMock()
        channel.call_unary_async = AsyncMock(side_effect=list(replies))
        return channel

    @staticmethod
    def _reply() -> bytes:
        return SendMessageResponse(
            message=new_text_message("Hello", role=Role.ROLE_AGENT)
        ).SerializeToString()

    @staticmethod
    def _rejection(code: Any) -> Exception:
        import slim_bindings

        rpc_error: Any = slim_bindings.RpcError
        error: Exception = rpc_error.Rpc(code=code, message="denied", details=None)
        return error

    @staticmethod
    async def _client(channel: MagicMock, *, requires_token: bool = True) -> Client:
        card = _make_agent_card([_iface("slimrpc", "org/ns/agent")])
        if requires_token:
            declare_identity_auth(card)
        return await A2AClientFactory(
            ClientConfig(slimrpc_channel_factory=lambda _url: channel, streaming=False)
        ).create(card)

    @staticmethod
    def _sent_metadata(channel: MagicMock, call: int = 0) -> Any:
        """``metadata``, the last positional argument of the stub's call."""
        return channel.call_unary_async.call_args_list[call].args[-1]

    @pytest.mark.asyncio
    async def test_token_is_sent_as_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        TestIdentityAuthOverJsonRpc._identity(monkeypatch)
        channel = self._channel(self._reply())
        client = await self._client(channel)

        _ = [e async for e in client.send_message(_user_request())]

        assert self._sent_metadata(channel) == {"Authorization": "Bearer tok"}

    @pytest.mark.asyncio
    async def test_token_is_sent_to_a_card_without_the_scheme(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        TestIdentityAuthOverJsonRpc._identity(monkeypatch)
        channel = self._channel(self._reply())
        client = await self._client(channel, requires_token=False)

        _ = [e async for e in client.send_message(_user_request())]

        assert self._sent_metadata(channel) == {"Authorization": "Bearer tok"}

    @pytest.mark.asyncio
    async def test_rejection_drops_the_token_for_a_card_without_the_scheme(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import slim_bindings

        sdk = TestIdentityAuthOverJsonRpc._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        sdk.access_token.return_value = TestIdentityAuthOverJsonRpc._cacheable_token(
            "tok"
        )
        rejection = self._rejection(slim_bindings.RpcCode.UNAUTHENTICATED)
        channel = self._channel(rejection, self._reply())
        client = await self._client(channel, requires_token=False)

        with pytest.raises(slim_bindings.RpcError):
            _ = [e async for e in client.send_message(_user_request())]
        _ = [e async for e in client.send_message(_user_request())]

        assert sdk.access_token.call_count == 2

    def test_overridden_rpcs_are_traced_and_close_once(self) -> None:
        from agntcy_app_sdk.semantic.a2a.client.factory import (
            _SRPC_TRACED_METHODS,
            _MetadataSRPCTransport,
        )

        for name in _SRPC_TRACED_METHODS:
            assert hasattr(_MetadataSRPCTransport.__dict__[name], "__wrapped__"), name
        assert "close" not in _MetadataSRPCTransport.__dict__
        assert not hasattr(SRPCTransport.__dict__["close"].__wrapped__, "__wrapped__")

    @pytest.mark.asyncio
    async def test_caller_service_parameters_are_sent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        TestIdentityAuthOverJsonRpc._identity(monkeypatch, enabled=False)
        channel = self._channel(self._reply())
        client = await self._client(channel)
        context = ClientCallContext(service_parameters={"X-Trace": "1"})

        _ = [e async for e in client.send_message(_user_request(), context=context)]

        assert self._sent_metadata(channel) == {"X-Trace": "1"}

    @pytest.mark.asyncio
    async def test_other_calls_send_metadata_too(self) -> None:
        from agntcy_app_sdk.semantic.a2a.client.factory import _MetadataSRPCTransport

        channel = self._channel(Task(id="t").SerializeToString())
        transport = _MetadataSRPCTransport(channel, _make_agent_card())
        context = ClientCallContext(service_parameters={"Authorization": "Bearer x"})

        task = await transport.get_task(GetTaskRequest(id="t"), context=context)

        assert task.id == "t"
        assert self._sent_metadata(channel) == {"Authorization": "Bearer x"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code_name", ["UNAUTHENTICATED", "PERMISSION_DENIED"])
    async def test_rejected_token_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch, code_name: str
    ) -> None:
        import slim_bindings

        sdk = TestIdentityAuthOverJsonRpc._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        old = TestIdentityAuthOverJsonRpc._cacheable_token("old")
        new = TestIdentityAuthOverJsonRpc._cacheable_token("new")
        sdk.access_token.side_effect = [old, new]
        rejection = self._rejection(getattr(slim_bindings.RpcCode, code_name))
        channel = self._channel(rejection, self._reply())
        client = await self._client(channel)

        with pytest.raises(slim_bindings.RpcError):
            _ = [e async for e in client.send_message(_user_request())]
        _ = [e async for e in client.send_message(_user_request())]

        assert self._sent_metadata(channel, 0) == {"Authorization": f"Bearer {old}"}
        assert self._sent_metadata(channel, 1) == {"Authorization": f"Bearer {new}"}

    @pytest.mark.asyncio
    async def test_other_errors_keep_the_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import slim_bindings

        sdk = TestIdentityAuthOverJsonRpc._identity(monkeypatch)
        monkeypatch.setattr(auth, "_token_cache", {})
        sdk.access_token.return_value = TestIdentityAuthOverJsonRpc._cacheable_token(
            "tok"
        )
        channel = self._channel(
            self._rejection(slim_bindings.RpcCode.INTERNAL), self._reply()
        )
        client = await self._client(channel)

        with pytest.raises(slim_bindings.RpcError):
            _ = [e async for e in client.send_message(_user_request())]
        _ = [e async for e in client.send_message(_user_request())]

        assert sdk.access_token.call_count == 1


# ---------------------------------------------------------------------------
# Interceptors — experimental operations
# ---------------------------------------------------------------------------


class TestA2AExperimentalClientInterceptors:
    @pytest.mark.asyncio
    async def test_broadcast_message_runs_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        await client.broadcast_message(_user_request(), recipients=["a"])

        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert [a.method for a in interceptor.after_calls] == ["send_message"]

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_runs_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        _ = [
            e
            async for e in client.broadcast_message_streaming(
                _user_request(), recipients=["a"]
            )
        ]

        assert [a.method for a in interceptor.before_calls] == [
            "send_message_streaming"
        ]
        assert len(interceptor.after_calls) == 1

    @pytest.mark.asyncio
    async def test_start_groupchat_runs_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.start_conversation = AsyncMock(return_value=[_rpc_response()])

        await client.start_groupchat(
            _user_request(), group_channel="zoo", participants=["a"]
        )

        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert len(interceptor.after_calls) == 1

    @pytest.mark.asyncio
    async def test_start_streaming_groupchat_runs_hooks(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.start_streaming_conversation = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        _ = [
            e
            async for e in client.start_streaming_groupchat(
                _user_request(), group_channel="zoo", participants=["a"]
            )
        ]

        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert len(interceptor.after_calls) == 1

    @pytest.mark.asyncio
    async def test_context_is_forwarded_to_interceptors(self) -> None:
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )
        context = ClientCallContext(state={"trace": "t-1"})

        await client.broadcast_message(
            _user_request(), recipients=["a"], context=context
        )

        assert interceptor.before_calls[0].context is context
        assert interceptor.after_calls[0].context is context

    @pytest.mark.asyncio
    async def test_context_defaults_to_none_without_interceptors(self) -> None:
        client, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        responses = await client.broadcast_message(_user_request(), recipients=["a"])

        assert len(responses) == 1

    @pytest.mark.asyncio
    async def test_interceptor_headers_reach_the_wire(self) -> None:
        """Headers an interceptor stores in service_parameters are sent."""
        interceptor = _RecordingInterceptor(headers={"X-Trace": "abc"})
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        await client.broadcast_message(_user_request(), recipients=["a"])

        _, msg = mock_transport.gather_stream.call_args.args
        assert msg.headers["X-Trace"] == "abc"

    @pytest.mark.asyncio
    async def test_interceptor_can_replace_the_request(self) -> None:
        class _Rewrite(ClientCallInterceptor):
            async def before(self, args: BeforeArgs) -> None:
                args.input = SendMessageRequest(
                    message=new_text_message("rewritten", role=Role.ROLE_USER)
                )

            async def after(self, args: AfterArgs) -> None:
                pass

        client, mock_transport, _ = _make_experimental([_Rewrite()])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        await client.broadcast_message(_user_request("original"), recipients=["a"])

        _, msg = mock_transport.gather_stream.call_args.args
        assert json.loads(msg.payload)["params"]["message"]["parts"][0]["text"] == (
            "rewritten"
        )
