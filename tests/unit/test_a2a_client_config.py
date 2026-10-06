# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AgentCard-centric A2A client stack:
ClientConfig, PatternsClientTransport, A2AExperimentalClient, A2AClientFactory.

Written against the a2a-sdk 1.x API: protobuf types, ``supported_interfaces``
(list order = server preference), ``before`` / ``after`` interceptors and
``StreamResponse`` events.
"""

import json
import warnings
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from a2a.client.client import Client, ClientCallContext
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
from a2a.utils.errors import TaskNotFoundError

from agntcy_app_sdk.semantic.a2a.client.config import (
    ClientConfig,
    NatsTransportConfig,
    SlimRpcConfig,
    SlimTransportConfig,
)
from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
    A2AExperimentalClient,
)
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.client.transports import (
    PatternsClientTransport,
    _parse_topic_from_url,
)

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


def _message_result(text: str = "Hello") -> dict:
    """ProtoJSON ``result`` carrying an agent message."""
    return {
        "message": {
            "messageId": str(uuid4()),
            "role": "ROLE_AGENT",
            "parts": [{"text": text}],
        }
    }


def _status_result(state: str = "TASK_STATE_WORKING", text: str = "tok") -> dict:
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
    result: dict | None = None,
    *,
    type_: str = "A2AResponse",
    status_code: int = 200,
    error: dict | None = None,
) -> MagicMock:
    """Create a mock transport response with a JSON-RPC payload."""
    resp = MagicMock()
    payload: dict = {"jsonrpc": "2.0", "id": "1"}
    if error is not None:
        payload["error"] = error
    else:
        payload["result"] = result if result is not None else _message_result()
    resp.payload = json.dumps(payload).encode("utf-8")
    resp.status_code = status_code
    resp.type = type_
    return resp


def _async_gen(*items):
    """Return a factory for an async generator yielding *items*."""

    async def _gen(*_args, **_kwargs):
        for item in items:
            yield item

    return _gen


def _sent_rpc(mock_call) -> dict:
    """Decode the JSON-RPC envelope from the transport ``Message`` that was sent."""
    message = mock_call.call_args.args[1]
    return json.loads(message.payload)


class _RecordingInterceptor(ClientCallInterceptor):
    """Interceptor recording ``before`` / ``after`` calls.

    Subclasses the real ``ClientCallInterceptor`` ABC so the tests verify the
    actual a2a-sdk 1.x interface contract.
    """

    def __init__(self, headers: dict[str, str] | None = None):
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
    def test_slim_transport_config_requires_fields(self):
        """SlimTransportConfig should require endpoint and name."""
        cfg = SlimTransportConfig(endpoint="http://localhost:46357", name="a/b/c")
        assert cfg.endpoint == "http://localhost:46357"
        assert cfg.name == "a/b/c"

    def test_slim_transport_config_missing_name_raises(self):
        with pytest.raises(TypeError):
            SlimTransportConfig(endpoint="http://localhost:46357")  # type: ignore[call-arg]

    def test_nats_transport_config_requires_endpoint(self):
        cfg = NatsTransportConfig(endpoint="nats://localhost:4222")
        assert cfg.endpoint == "nats://localhost:4222"

    def test_nats_transport_config_missing_endpoint_raises(self):
        with pytest.raises(TypeError):
            NatsTransportConfig()  # type: ignore[call-arg]

    def test_slim_rpc_config_requires_all_fields(self):
        cfg = SlimRpcConfig(namespace="agntcy", group="demo", name="client")
        assert cfg.namespace == "agntcy"
        assert cfg.group == "demo"
        assert cfg.name == "client"


# ---------------------------------------------------------------------------
# ClientConfig tests
# ---------------------------------------------------------------------------


class TestClientConfig:
    def test_extends_upstream_config(self):
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
        """Empty config should auto-derive supported_protocol_bindings with JSONRPC."""
        assert ClientConfig().supported_protocol_bindings == ["JSONRPC"]

    def test_post_init_slim_config(self):
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        assert "JSONRPC" in config.supported_protocol_bindings
        assert "slimpatterns" in config.supported_protocol_bindings

    def test_post_init_slim_transport(self):
        config = ClientConfig(slim_transport=_make_mock_transport())
        assert "slimpatterns" in config.supported_protocol_bindings

    def test_post_init_nats_config(self):
        config = ClientConfig(
            nats_config=NatsTransportConfig(endpoint="nats://localhost:4222"),
        )
        assert "natspatterns" in config.supported_protocol_bindings

    def test_post_init_nats_transport(self):
        config = ClientConfig(nats_transport=_make_mock_transport("NATS"))
        assert "natspatterns" in config.supported_protocol_bindings

    def test_post_init_slimrpc_channel_factory(self):
        config = ClientConfig(slimrpc_channel_factory=MagicMock())
        assert "slimrpc" in config.supported_protocol_bindings

    def test_post_init_slimrpc_config(self):
        config = ClientConfig(
            slimrpc_config=SlimRpcConfig(
                namespace="agntcy", group="demo", name="client"
            ),
        )
        assert "slimrpc" in config.supported_protocol_bindings

    def test_post_init_multiple_transports(self):
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

    def test_explicit_bindings_not_overridden(self):
        """If the user sets supported_protocol_bindings, __post_init__ keeps them."""
        config = ClientConfig(
            supported_protocol_bindings=["custom_transport"],
            slim_transport=_make_mock_transport(),
        )
        assert config.supported_protocol_bindings == ["custom_transport"]

    def test_explicit_bindings_are_normalised(self):
        """Casing and aliases are normalised to what the upstream factory expects."""
        config = ClientConfig(supported_protocol_bindings=["jsonrpc", "SLIM", "nats"])
        assert config.supported_protocol_bindings == [
            "JSONRPC",
            "slimpatterns",
            "natspatterns",
        ]

    def test_deprecated_supported_transports_is_honoured_with_warning(self):
        """The a2a-sdk 0.3 field name still works, but warns."""
        with pytest.warns(DeprecationWarning, match="supported_transports"):
            config = ClientConfig(
                supported_transports=["custom_transport"],
                slim_transport=_make_mock_transport(),
            )
        assert config.supported_protocol_bindings == ["custom_transport"]

    def test_deprecated_supported_transports_mirrors_bindings(self):
        """Reading the old attribute keeps working (mirrors the new field)."""
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            config = ClientConfig(slim_transport=_make_mock_transport())
        assert config.supported_transports == config.supported_protocol_bindings


# ---------------------------------------------------------------------------
# _parse_topic_from_url tests
# ---------------------------------------------------------------------------


class TestParseTopicFromUrl:
    def test_slim_scheme(self):
        assert _parse_topic_from_url("slim://my_topic") == "my_topic"

    def test_nats_scheme(self):
        assert _parse_topic_from_url("nats://my_topic") == "my_topic"

    def test_plain_topic(self):
        assert _parse_topic_from_url("my_topic") == "my_topic"

    def test_http_url_passthrough(self):
        """HTTP URLs should pass through unchanged (not a patterns scheme)."""
        assert _parse_topic_from_url("http://localhost:9999") == "http://localhost:9999"

    def test_topic_with_slashes(self):
        assert (
            _parse_topic_from_url("slim://default/default/agent")
            == "default/default/agent"
        )

    def test_slim_endpoint_with_port(self):
        assert _parse_topic_from_url("slim://localhost:46357/my_topic") == "my_topic"

    def test_nats_endpoint_with_port(self):
        assert _parse_topic_from_url("nats://localhost:4222/my_topic") == "my_topic"

    def test_slim_endpoint_with_port_and_slashes(self):
        assert (
            _parse_topic_from_url("slim://localhost:46357/default/default/agent")
            == "default/default/agent"
        )


# ---------------------------------------------------------------------------
# PatternsClientTransport tests
# ---------------------------------------------------------------------------


class TestPatternsClientTransport:
    def test_create_slim_eager(self):
        """create() should use slim_transport from config for slim interfaces."""
        mock_transport = _make_mock_transport("SLIM")
        config = ClientConfig(slim_transport=mock_transport)
        card = _make_agent_card([_iface("slimpatterns", "slim://topic_1")])

        transport = PatternsClientTransport.create(card, "slim://topic_1", config)
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"
        assert transport._agent_card is card

    def test_create_nats_eager(self):
        mock_transport = _make_mock_transport("NATS")
        config = ClientConfig(nats_transport=mock_transport)
        card = _make_agent_card([_iface("natspatterns", "nats://topic_1")])

        transport = PatternsClientTransport.create(card, "nats://topic_1", config)
        assert transport._transport is mock_transport
        assert transport._topic == "topic_1"

    def test_create_no_transport_raises(self):
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

    def test_create_unknown_transport_raises(self):
        config = ClientConfig(supported_protocol_bindings=["unknown"])
        card = _make_agent_card([_iface("unknown", "topic_1")])

        with pytest.raises(ValueError, match="No pre-built transport"):
            PatternsClientTransport.create(card, "topic_1", config)

    @pytest.mark.asyncio
    async def test_send_message(self):
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
    async def test_send_message_returns_task_result(self):
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
    async def test_send_message_forwards_headers(self):
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
    async def test_json_rpc_error_raises_matching_a2a_error(self):
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            error={"code": -32001, "message": "no such task"}
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        with pytest.raises(TaskNotFoundError):
            await pct.get_task(GetTaskRequest(id="missing"))

    @pytest.mark.asyncio
    async def test_forbidden_send_message_returns_agent_message(self):
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
    async def test_get_task(self):
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
    async def test_cancel_task(self):
        mock_transport = _make_mock_transport()
        mock_transport.request.return_value = _rpc_response(
            {"id": "t1", "status": {"state": "TASK_STATE_CANCELED"}}
        )
        pct = PatternsClientTransport(mock_transport, _make_agent_card(), "test_topic")

        task = await pct.cancel_task(CancelTaskRequest(id="t1"))

        assert task.status.state == TaskState.TASK_STATE_CANCELED
        assert _sent_rpc(mock_transport.request)["method"] == "CancelTask"

    @pytest.mark.asyncio
    async def test_send_message_streaming_yields_events_and_stops_at_final(self):
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
    async def test_send_message_streaming_falls_back_to_unary(self):
        """If the transport cannot stream, a single SendMessage is used."""

        async def _unsupported(*_a, **_k):
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
    async def test_get_extended_agent_card_returns_cached_card(self):
        """Without an advertised extended card, no request is made."""
        mock_transport = _make_mock_transport()
        card = _make_agent_card()
        pct = PatternsClientTransport(mock_transport, card, "test_topic")

        result = await pct.get_extended_agent_card(GetExtendedAgentCardRequest())

        assert result is card
        mock_transport.request.assert_not_called()

    @pytest.mark.asyncio
    async def test_subscribe_not_supported(self):
        pct = PatternsClientTransport(
            _make_mock_transport(), _make_agent_card(), "test_topic"
        )
        with pytest.raises(NotImplementedError):
            async for _ in pct.subscribe(SubscribeToTaskRequest(id="t1")):
                pass

    @pytest.mark.asyncio
    async def test_close(self):
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
    def test_properties(self):
        mock_client = MagicMock()
        experimental, mock_transport, card = _make_experimental(inner=mock_client)

        assert experimental.agent_card is card
        assert experimental.upstream_client is mock_client
        assert experimental.transport is mock_transport
        assert experimental.topic == "test_topic"

    def test_is_client_subclass(self):
        experimental, _, _ = _make_experimental()
        assert isinstance(experimental, Client)

    def test_experimental_methods_available(self):
        experimental, _, _ = _make_experimental()

        assert callable(experimental.broadcast_message)
        assert callable(experimental.broadcast_message_streaming)
        assert callable(experimental.start_groupchat)
        assert callable(experimental.start_streaming_groupchat)

    @pytest.mark.asyncio
    async def test_get_extended_agent_card_delegates(self):
        inner = MagicMock()
        inner.get_extended_agent_card = AsyncMock(return_value="card")
        experimental, _, _ = _make_experimental(inner=inner)

        request = GetExtendedAgentCardRequest()
        result = await experimental.get_extended_agent_card(request)

        assert result == "card"
        inner.get_extended_agent_card.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_send_message_delegates(self):
        inner = MagicMock()
        expected = StreamResponse()
        expected.message.CopyFrom(new_text_message("hello"))

        async def _send(request, *, context=None):
            yield expected

        inner.send_message = _send
        experimental, _, _ = _make_experimental(inner=inner)

        events = [e async for e in experimental.send_message(_user_request())]
        assert events == [expected]

    @pytest.mark.asyncio
    async def test_close_closes_inner_client(self):
        inner = MagicMock()
        inner.close = AsyncMock()
        experimental, _, _ = _make_experimental(inner=inner)

        await experimental.close()
        inner.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_add_interceptor_registers_on_both_clients(self):
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
    async def test_broadcast_message_sends_send_message_rpc(self):
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
    async def test_broadcast_message_skips_intermediate_status_updates(self):
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
    async def test_broadcast_message_streaming_yields_all_events(self):
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
    async def test_broadcast_message_streaming_stops_after_expected_finals(self):
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
    async def test_broadcast_forbidden_response_becomes_agent_message(self):
        experimental, mock_transport, _ = _make_experimental()
        forbidden = MagicMock()
        forbidden.payload = json.dumps({"error": "forbidden"}).encode("utf-8")
        forbidden.status_code = 403
        forbidden.type = "A2AResponse"
        mock_transport.gather_stream = MagicMock(side_effect=_async_gen(forbidden))

        responses = await experimental.broadcast_message(
            _user_request(), recipients=["a"]
        )

        assert len(responses) == 1
        assert "Forbidden" in responses[0].message.parts[0].text

    @pytest.mark.asyncio
    async def test_start_groupchat(self):
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
    async def test_start_streaming_groupchat(self):
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
    def test_constructor_default_config(self):
        factory = A2AClientFactory()
        assert factory._config.supported_protocol_bindings == ["JSONRPC"]

    def test_constructor_with_config(self):
        config = ClientConfig(
            slim_config=SlimTransportConfig(
                endpoint="http://localhost:46357", name="a/b/c"
            ),
        )
        factory = A2AClientFactory(config)
        assert factory._config is config
        assert "slimpatterns" in factory._config.supported_protocol_bindings

    # -- Negotiation tests --------------------------------------------------

    def test_negotiate_server_preference(self):
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

    def test_negotiate_fallback_to_jsonrpc(self):
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

    def test_negotiate_no_match_raises(self):
        factory = A2AClientFactory(
            ClientConfig(supported_protocol_bindings=["custom_only"])
        )

        card = _make_agent_card([_iface("grpc", "grpc://agent")])
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    def test_negotiate_empty_card_raises(self):
        factory = A2AClientFactory(ClientConfig())
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(_make_agent_card([]))

    def test_negotiate_client_preference(self):
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

    def test_select_card_keeps_only_chosen_interface(self):
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

    def test_select_card_url_override(self):
        card = _make_agent_card([_iface("slimrpc", "slim://host:46357/org/ns/agent")])
        selected = A2AClientFactory._select_card(
            card, "slimrpc", "slim://host:46357/org/ns/agent", url="org/ns/agent"
        )
        assert selected.supported_interfaces[0].url == "org/ns/agent"
        assert selected.supported_interfaces[0].protocol_binding == "slimrpc"

    # -- create() async path tests ------------------------------------------

    @pytest.mark.asyncio
    async def test_create_with_eager_slim_transport(self):
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
    async def test_create_with_eager_nats_transport(self):
        mock_transport = _make_mock_transport("NATS")
        factory = A2AClientFactory(ClientConfig(nats_transport=mock_transport))

        card = _make_agent_card([_iface("natspatterns", "nats://my_agent")])
        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport is mock_transport
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_jsonrpc_returns_upstream_client(self):
        """JSONRPC returns the upstream Client, not A2AExperimentalClient."""
        factory = A2AClientFactory(ClientConfig())

        result = await factory.create(_make_agent_card())  # defaults to JSONRPC

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)

    @pytest.mark.asyncio
    async def test_create_accepts_lowercase_jsonrpc_binding(self):
        """Cards written with the SDK's lowercase constant still negotiate."""
        factory = A2AClientFactory(ClientConfig())

        result = await factory.create(
            _make_agent_card([_iface("jsonrpc", "http://localhost:8080")])
        )

        assert isinstance(result, Client)

    @pytest.mark.asyncio
    async def test_create_does_not_mutate_card(self):
        factory = A2AClientFactory(ClientConfig())
        card = _make_agent_card([_iface("jsonrpc", "http://localhost:8080")])
        before = AgentCard()
        before.CopyFrom(card)

        await factory.create(card)

        assert card == before

    @pytest.mark.asyncio
    async def test_create_deferred_slim_missing_config_raises(self):
        """slimpatterns negotiated but neither config nor transport set → error."""
        config = ClientConfig(supported_protocol_bindings=["slimpatterns", "JSONRPC"])
        factory = A2AClientFactory(config)

        card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])
        with pytest.raises(ValueError, match="neither slim_transport nor slim_config"):
            await factory.create(card)

    @pytest.mark.asyncio
    async def test_create_consumers_are_deprecated_and_ignored(self):
        """a2a-sdk 1.x removed client consumers; passing them warns."""
        factory = A2AClientFactory(ClientConfig())

        with pytest.warns(DeprecationWarning, match="consumers"):
            result = await factory.create(_make_agent_card(), consumers=[AsyncMock()])

        assert isinstance(result, Client)

    # -- connect() classmethod test -----------------------------------------

    @pytest.mark.asyncio
    async def test_connect_with_card(self):
        """connect() with an AgentCard should skip HTTP resolution."""
        result = await A2AClientFactory.connect(
            _make_agent_card(), config=ClientConfig()
        )
        assert isinstance(result, Client)

    @pytest.mark.asyncio
    async def test_connect_backfills_jsonrpc_interface_for_bare_card(self):
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


def _make_multi_transport_factory():
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

    def test_bindings_contain_all(self):
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
    def test_server_preference_per_transport(self, binding, url):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface(binding, url)])

        assert factory._negotiate(card) == (binding, url)

    def test_first_listed_interface_wins(self):
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

    def test_unsupported_first_falls_back_to_next_supported(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [
                _iface("grpc", "grpc://agent"),
                _iface("natspatterns", "nats://agent_topic"),
                _iface("JSONRPC", "http://localhost:9999"),
            ]
        )

        assert factory._negotiate(card) == ("natspatterns", "nats://agent_topic")

    def test_client_preference_overrides_server(self):
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

    def test_no_match_raises(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [_iface("grpc", "grpc://agent"), _iface("websocket", "ws://agent")]
        )
        with pytest.raises(ValueError, match="No compatible transports"):
            factory._negotiate(card)

    # -- create() dispatches to correct path --------------------------------

    @pytest.mark.asyncio
    async def test_create_dispatches_slimrpc(self):
        """slimrpc goes through the upstream sync path → upstream Client."""
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimrpc", "default/default/agent")])

        result = await factory.create(card)

        assert isinstance(result, Client)
        assert not isinstance(result, A2AExperimentalClient)

    @pytest.mark.asyncio
    async def test_create_slimrpc_strips_slim_scheme_from_url(self):
        """The channel factory receives a bare ``org/ns/name`` identity."""
        factory, config = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimrpc", "slim://host:46357/org/ns/agent")])

        await factory.create(card)

        config.slimrpc_channel_factory.assert_called_once_with("org/ns/agent")

    @pytest.mark.asyncio
    async def test_create_dispatches_slimpatterns(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slimpatterns", "slim://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_natspatterns(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("natspatterns", "nats://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_dispatches_jsonrpc_after_unsupported(self):
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
    def test_negotiate_alias_binding(self, alias, url):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface(alias, url)])

        assert factory._negotiate(card) == (alias, url)

    def test_negotiate_alias_after_unsupported(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card(
            [_iface("grpc", "grpc://agent"), _iface("slim", "slim://my_topic")]
        )

        assert factory._negotiate(card) == ("slim", "slim://my_topic")

    @pytest.mark.asyncio
    async def test_create_slim_alias_dispatches_to_slimpatterns(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("slim", "slim://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "SLIM"
        assert result.topic == "my_agent"

    @pytest.mark.asyncio
    async def test_create_nats_alias_dispatches_to_natspatterns(self):
        factory, _ = _make_multi_transport_factory()
        card = _make_agent_card([_iface("nats", "nats://my_agent")])

        result = await factory.create(card)

        assert isinstance(result, A2AExperimentalClient)
        assert result.transport.type() == "NATS"
        assert result.topic == "my_agent"

    def test_client_preference_resolves_aliases(self):
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
    async def test_slimrpc_uses_trailing_slash_endpoint(self):
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
    async def test_slimrpc_noop_when_eager_factory_set(self):
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
    async def test_send_message_runs_before_and_after_hooks(self):
        interceptor = _RecordingInterceptor()
        client, mock_transport = await _patterns_client([interceptor])
        mock_transport.request.return_value = _rpc_response(_message_result("Hello"))

        events = [e async for e in client.send_message(_user_request())]

        assert [get_stream_response_text(e) for e in events] == ["Hello"]
        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert [a.method for a in interceptor.after_calls] == ["send_message"]
        assert interceptor.before_calls[0].agent_card.name == "test-agent"

    @pytest.mark.asyncio
    async def test_streaming_send_message_uses_streaming_hooks(self):
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
    async def test_interceptor_headers_reach_the_wire(self):
        """service_parameters set in ``before`` become transport message headers."""
        interceptor = _RecordingInterceptor(headers={"Authorization": "Bearer abc"})
        client, mock_transport = await _patterns_client([interceptor])
        mock_transport.request.return_value = _rpc_response()

        _ = [e async for e in client.send_message(_user_request())]

        sent = mock_transport.request.call_args.args[1]
        assert sent.headers["Authorization"] == "Bearer abc"

    @pytest.mark.asyncio
    async def test_get_task_runs_hooks(self):
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
    async def test_interceptor_chaining_order(self):
        """``before`` runs in registration order, ``after`` in reverse."""
        order: list[str] = []

        class _Ordered(ClientCallInterceptor):
            def __init__(self, label: str):
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
    async def test_no_interceptors_passthrough(self):
        client, mock_transport = await _patterns_client()
        mock_transport.request.return_value = _rpc_response(_message_result("plain"))

        events = [e async for e in client.send_message(_user_request())]

        assert get_stream_response_text(events[0]) == "plain"


# ---------------------------------------------------------------------------
# Interceptors — experimental operations
# ---------------------------------------------------------------------------


class TestA2AExperimentalClientInterceptors:
    @pytest.mark.asyncio
    async def test_broadcast_message_runs_hooks(self):
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        await client.broadcast_message(_user_request(), recipients=["a"])

        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert [a.method for a in interceptor.after_calls] == ["send_message"]

    @pytest.mark.asyncio
    async def test_broadcast_message_streaming_runs_hooks(self):
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
    async def test_start_groupchat_runs_hooks(self):
        interceptor = _RecordingInterceptor()
        client, mock_transport, _ = _make_experimental([interceptor])
        mock_transport.start_conversation = AsyncMock(return_value=[_rpc_response()])

        await client.start_groupchat(
            _user_request(), group_channel="zoo", participants=["a"]
        )

        assert [a.method for a in interceptor.before_calls] == ["send_message"]
        assert len(interceptor.after_calls) == 1

    @pytest.mark.asyncio
    async def test_start_streaming_groupchat_runs_hooks(self):
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
    async def test_context_is_forwarded_to_interceptors(self):
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
    async def test_context_defaults_to_none_without_interceptors(self):
        client, mock_transport, _ = _make_experimental()
        mock_transport.gather_stream = MagicMock(
            side_effect=_async_gen(_rpc_response())
        )

        responses = await client.broadcast_message(_user_request(), recipients=["a"])

        assert len(responses) == 1

    @pytest.mark.asyncio
    async def test_interceptor_headers_reach_the_wire(self):
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
    async def test_interceptor_can_replace_the_request(self):
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
