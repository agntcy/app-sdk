# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Tests for the A2A server side on a2a-sdk 1.x:

* ``A2AServerConfig`` (and the deprecated ``A2AStarletteApplication`` shim)
* the HTTP JSON-RPC app it builds
* interface declaration done by the server handlers
* the patterns bridge (``A2AExperimentalServer``) exercised end-to-end against
  the real client stack over an in-memory loopback transport (no SLIM / NATS
  needed).
"""

import asyncio
import json
import warnings
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from a2a.client.client import ClientCallContext
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    GetTaskRequest,
    Role,
    SendMessageRequest,
    TaskState,
)
from a2a.utils.errors import TaskNotFoundError
from google.protobuf.json_format import MessageToDict
from starlette.testclient import TestClient

from agntcy_app_sdk.semantic.a2a.card_utils import preferred_transport
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.client.experimental_patterns import (
    A2AExperimentalClient,
)
from agntcy_app_sdk.semantic.a2a.client.factory import A2AClientFactory
from agntcy_app_sdk.semantic.a2a.client.transports import PatternsClientTransport
from agntcy_app_sdk.semantic.a2a.server import A2AServerConfig, A2AStarletteApplication
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    A2AExperimentalServer,
    A2AExperimentalServerHandler,
)
from agntcy_app_sdk.semantic.message import Message
from tests.server.agent_executor import (
    HelloWorldAgentExecutor,
    HelloWorldStreamingAgentExecutor,
)

pytest_plugins = "pytest_asyncio"


@pytest_asyncio.fixture(autouse=True)
async def _cancel_background_tasks():
    """Cancel a2a-sdk per-request background tasks before the loop closes.

    ``DefaultRequestHandler`` keeps ``ActiveTask`` producer/consumer tasks
    alive briefly after a response; without this they are destroyed pending
    when pytest-asyncio tears the loop down, spamming the output.
    """
    yield
    current = asyncio.current_task()
    pending = [t for t in asyncio.all_tasks() if t is not current and not t.done()]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _card(
    interfaces: list[AgentInterface] | None = None, streaming: bool = False
) -> AgentCard:
    return AgentCard(
        name="Hello World Agent",
        description="test",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=streaming),
        supported_interfaces=interfaces,
    )


def _config(
    card: AgentCard | None = None, streaming: bool = False, name: str = "agent"
) -> A2AServerConfig:
    card = card or _card(streaming=streaming)
    executor = (
        HelloWorldStreamingAgentExecutor(name)
        if streaming
        else HelloWorldAgentExecutor(name)
    )
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    return A2AServerConfig(agent_card=card, request_handler=handler)


def _user_request(text: str = "hi") -> SendMessageRequest:
    return SendMessageRequest(message=new_text_message(text, role=Role.ROLE_USER))


# ---------------------------------------------------------------------------
# A2AServerConfig / deprecated A2AStarletteApplication
# ---------------------------------------------------------------------------


class TestA2AServerConfig:
    def test_holds_card_and_handler(self):
        config = _config()
        assert config.agent_card.name == "Hello World Agent"
        assert config.request_handler is not None
        assert config.rpc_url == "/"
        assert config.enable_v0_3_compat is False

    def test_is_exported_from_package_roots(self):
        import agntcy_app_sdk
        import agntcy_app_sdk.semantic.a2a as a2a_pkg

        assert agntcy_app_sdk.A2AServerConfig is A2AServerConfig
        assert a2a_pkg.A2AServerConfig is A2AServerConfig

    def test_build_app_serves_agent_card(self):
        card = _card([AgentInterface(protocol_binding="JSONRPC", url="http://h:1/")])
        app = _config(card).build_app()

        response = TestClient(app).get("/.well-known/agent-card.json")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Hello World Agent"
        assert body["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"

    def test_build_app_serves_jsonrpc_send_message(self):
        app = _config().build_app()
        payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": "m1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "hi"}],
                }
            },
        }

        response = TestClient(app).post(
            "/", json=payload, headers={"A2A-Version": "1.0"}
        )

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == "1"
        assert body["result"]["message"]["parts"][0]["text"] == "Hello from agent"

    @pytest.mark.asyncio
    async def test_card_modifier_is_applied_to_served_card(self):
        async def modifier(card: AgentCard) -> AgentCard:
            card.description = "modified"
            return card

        config = _config()
        config.card_modifier = modifier

        response = TestClient(config.build_app()).get("/.well-known/agent-card.json")

        assert response.json()["description"] == "modified"


class TestDeprecatedA2AStarletteApplication:
    def test_emits_deprecation_warning(self):
        config = _config()
        with pytest.warns(DeprecationWarning, match="A2AServerConfig"):
            A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

    def test_behaves_as_server_config(self):
        config = _config()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

        assert isinstance(shim, A2AServerConfig)
        assert shim.agent_card is config.agent_card
        assert shim.request_handler is config.request_handler

    def test_legacy_attribute_and_method_aliases(self):
        config = _config()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

        assert shim.http_handler is config.request_handler
        response = TestClient(shim.build()).get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_session_accepts_the_shim(self):
        """``session.add(A2AStarletteApplication(...))`` keeps working."""
        from agntcy_app_sdk.factory import AgntcyFactory

        config = _config()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )
        session = AgntcyFactory().create_app_session(max_sessions=1)

        session.add(shim).with_host("127.0.0.1").with_port(9111).with_session_id(
            "shim"
        ).build()

        assert session.get_app_container("shim") is not None


# ---------------------------------------------------------------------------
# Interface declaration by the server handlers
# ---------------------------------------------------------------------------


def _mock_transport(kind: str) -> MagicMock:
    transport = MagicMock()
    transport.type.return_value = kind
    transport.setup = AsyncMock()
    transport.close = AsyncMock()
    transport.subscribe = AsyncMock()
    transport.set_callback = MagicMock()
    return transport


class TestInterfaceDeclaration:
    @pytest.mark.asyncio
    async def test_handler_declares_missing_patterns_interface_as_preferred(self):
        card = _card([AgentInterface(protocol_binding="JSONRPC", url="http://h:1/")])
        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM"), topic="my_topic"
        )

        await handler.setup()

        interfaces = [(i.protocol_binding, i.url) for i in card.supported_interfaces]
        assert interfaces == [
            ("slimpatterns", "slim://my_topic"),
            ("JSONRPC", "http://h:1/"),
        ]
        assert preferred_transport(card) == "slimpatterns"

    @pytest.mark.asyncio
    async def test_handler_leaves_author_declared_interface_untouched(self):
        """URL (explicit endpoint) and position (author's preference) are kept."""
        card = _card(
            [
                AgentInterface(protocol_binding="JSONRPC", url="http://h:1/"),
                AgentInterface(
                    protocol_binding="slimpatterns",
                    url="slim://localhost:46357/declared",
                ),
            ]
        )
        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM"), topic="other_topic"
        )

        await handler.setup()

        interfaces = [(i.protocol_binding, i.url) for i in card.supported_interfaces]
        assert interfaces == [
            ("JSONRPC", "http://h:1/"),
            ("slimpatterns", "slim://localhost:46357/declared"),
        ]

    @pytest.mark.asyncio
    async def test_nats_transport_declares_natspatterns(self):
        card = _card()
        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("NATS"), topic="t"
        )

        await handler.setup()

        assert [(i.protocol_binding, i.url) for i in card.supported_interfaces] == [
            ("natspatterns", "nats://t")
        ]

    def test_create_client_card_makes_patterns_interface_preferred(self):
        base = _card([AgentInterface(protocol_binding="JSONRPC", url="http://h:1/")])

        client_card = A2AExperimentalServer.create_client_card(
            base, "SLIM", topic="my_topic"
        )

        assert preferred_transport(client_card) == "slimpatterns"
        assert client_card.supported_interfaces[0].url == "slim://my_topic"
        # the base card is not mutated
        assert [i.protocol_binding for i in base.supported_interfaces] == ["JSONRPC"]

    def test_create_client_card_replaces_declared_url(self):
        base = _card(
            [AgentInterface(protocol_binding="slimpatterns", url="slim://old")]
        )

        client_card = A2AExperimentalServer.create_client_card(
            base, "SLIM", topic="new"
        )

        assert [
            (i.protocol_binding, i.url) for i in client_card.supported_interfaces
        ] == [("slimpatterns", "slim://new")]

    def test_create_client_card_derives_topic_from_card_name(self):
        client_card = A2AExperimentalServer.create_client_card(_card(), "NATS")

        assert preferred_transport(client_card) == "natspatterns"
        assert client_card.supported_interfaces[0].url.startswith("nats://")


# ---------------------------------------------------------------------------
# Patterns bridge over an in-memory loopback transport
# ---------------------------------------------------------------------------


class _LoopbackTransport:
    """Minimal ``BaseTransport`` stand-in wiring client calls straight into an
    ``A2AExperimentalServer`` — the same path SLIM / NATS would carry."""

    def __init__(self, server: A2AExperimentalServer):
        self._server = server
        self.sent: list[Message] = []

    def type(self) -> str:
        return "SLIM"

    async def setup(self) -> None:  # pragma: no cover - trivial
        pass

    async def close(self) -> None:  # pragma: no cover - trivial
        pass

    async def request(self, topic: str, message: Message, **_kwargs) -> Message:
        self.sent.append(message)
        return await self._server.handle_message(message)

    async def request_stream(self, topic: str, message: Message, **_kwargs):
        self.sent.append(message)
        published: list[Message] = []

        async def _publish(msg: Message) -> None:
            published.append(msg)

        final = await self._server.handle_message(message, publish_fn=_publish)
        for item in published:
            yield item
        yield final


def _loopback(streaming: bool) -> tuple[A2AExperimentalServer, _LoopbackTransport]:
    server = A2AExperimentalServer()
    server.bind_server(_config(streaming=streaming))
    return server, _LoopbackTransport(server)


async def _loopback_client(streaming: bool):
    _, transport = _loopback(streaming)
    card = _card(
        [AgentInterface(protocol_binding="slimpatterns", url="slim://agent")],
        streaming=streaming,
    )
    factory = A2AClientFactory(
        ClientConfig(slim_transport=transport, streaming=streaming)  # type: ignore[arg-type]
    )
    client = await factory.create(card)
    assert isinstance(client, A2AExperimentalClient)
    return client, transport


class TestPatternsBridgeLoopback:
    @pytest.mark.asyncio
    async def test_unary_send_message_round_trip(self):
        client, _ = await _loopback_client(streaming=False)

        events = [e async for e in client.send_message(_user_request())]

        assert len(events) == 1
        assert events[0].HasField("message")
        assert get_stream_response_text(events[0]) == "Hello from agent"

    @pytest.mark.asyncio
    async def test_streaming_send_message_yields_task_then_status_updates(self):
        client, _ = await _loopback_client(streaming=True)

        events = [e async for e in client.send_message(_user_request())]

        kinds = [e.WhichOneof("payload") for e in events]
        assert kinds[0] == "task"
        assert set(kinds[1:]) == {"status_update"}
        states = [
            e.status_update.status.state for e in events if e.HasField("status_update")
        ]
        assert states[-1] == TaskState.TASK_STATE_COMPLETED
        assert states.count(TaskState.TASK_STATE_COMPLETED) == 1
        assert TaskState.TASK_STATE_WORKING in states
        streamed_text = [
            get_stream_response_text(e) for e in events if e.HasField("status_update")
        ]
        assert "Hello" in streamed_text

    @pytest.mark.asyncio
    async def test_get_task_after_streaming_run(self):
        client, _ = await _loopback_client(streaming=True)
        events = [e async for e in client.send_message(_user_request())]
        task_id = events[0].task.id

        task = await client.get_task(GetTaskRequest(id=task_id))

        assert task.id == task_id
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_unknown_task_raises_task_not_found(self):
        client, _ = await _loopback_client(streaming=False)

        with pytest.raises(TaskNotFoundError):
            await client.get_task(GetTaskRequest(id="does-not-exist"))

    @pytest.mark.asyncio
    async def test_request_headers_reach_the_server_context(self):
        server, transport = _loopback(streaming=False)
        seen: dict = {}
        original = server._handler.on_message_send

        async def _spy(request, context):
            seen.update(context.state["headers"])
            seen["method"] = context.state["method"]
            return await original(request, context)

        server._handler.on_message_send = _spy
        pct = PatternsClientTransport(
            transport,  # type: ignore[arg-type]
            _card([AgentInterface(protocol_binding="slimpatterns", url="slim://a")]),
            "a",
        )

        await pct.send_message(
            _user_request(),
            context=ClientCallContext(service_parameters={"X-Trace": "t-1"}),
        )

        assert seen["X-Trace"] == "t-1"
        assert seen["method"] == "SendMessage"

    @pytest.mark.asyncio
    async def test_broadcast_message_round_trip(self):
        client, transport = await _loopback_client(streaming=False)

        async def _gather_stream(topic, message, **_kwargs):
            yield await transport.request(topic, message)

        transport.gather_stream = _gather_stream  # type: ignore[attr-defined]

        responses = await client.broadcast_message(_user_request(), recipients=["x"])

        assert len(responses) == 1
        assert responses[0].message.parts[0].text == "Hello from agent"


class TestPatternsBridgeProtocol:
    """Direct ``handle_message`` checks: JSON-RPC envelope handling."""

    @staticmethod
    async def _call(server: A2AExperimentalServer, body) -> dict:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        reply = await server.handle_message(
            Message(type="A2ARequest", payload=payload, headers={})
        )
        assert reply.type == "A2AResponse"
        return json.loads(reply.payload)

    @pytest.mark.asyncio
    async def test_invalid_json_is_a_parse_error(self):
        server, _ = _loopback(streaming=False)
        reply = await self._call(server, b"{not json")
        assert reply["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method_is_invalid_request(self):
        server, _ = _loopback(streaming=False)
        reply = await self._call(server, {"jsonrpc": "2.0", "id": "1"})
        assert reply["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self):
        server, _ = _loopback(streaming=False)
        reply = await self._call(
            server, {"jsonrpc": "2.0", "id": "1", "method": "message/send"}
        )
        assert reply["error"]["code"] == -32601
        assert reply["id"] == "1"

    @pytest.mark.asyncio
    async def test_invalid_params_is_invalid_params(self):
        server, _ = _loopback(streaming=False)
        reply = await self._call(
            server,
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "SendMessage",
                "params": {"message": {"role": "NOT_A_ROLE"}},
            },
        )
        assert reply["error"]["code"] == -32602

    @pytest.mark.asyncio
    async def test_relayed_response_is_rewrapped_as_send_message(self):
        """A JSON-RPC *response* carrying a message (relay) is accepted as input."""
        server, _ = _loopback(streaming=False)
        relayed = {
            "jsonrpc": "2.0",
            "id": "upstream",
            "result": {
                "message": {
                    "messageId": "m-relay",
                    "role": "ROLE_AGENT",
                    "parts": [{"text": "from the previous agent"}],
                }
            },
        }

        reply = await self._call(server, relayed)

        assert "error" not in reply
        assert reply["result"]["message"]["parts"][0]["text"] == "Hello from agent"

    @pytest.mark.asyncio
    async def test_streaming_publishes_intermediates_then_final(self):
        server, _ = _loopback(streaming=True)
        published: list[Message] = []

        async def _publish(msg: Message) -> None:
            published.append(msg)

        reply = await server.handle_message(
            Message(
                type="A2ARequest",
                payload=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": "s1",
                        "method": "SendStreamingMessage",
                        "params": MessageToDict(_user_request()),
                    }
                ).encode(),
                headers={},
            ),
            publish_fn=_publish,
        )

        assert published, "expected intermediate status updates"
        assert all(m.type == "A2AStatusUpdate" for m in published)
        final = json.loads(reply.payload)
        assert reply.type == "A2AResponse"
        assert (
            final["result"]["statusUpdate"]["status"]["state"] == "TASK_STATE_COMPLETED"
        )
        # intermediates + final all share the request id
        assert {json.loads(m.payload)["id"] for m in published} == {"s1"}

    @pytest.mark.asyncio
    async def test_streaming_without_publish_fn_returns_last_item(self):
        server, _ = _loopback(streaming=True)

        reply = await self._call(
            server,
            {
                "jsonrpc": "2.0",
                "id": "s2",
                "method": "SendStreamingMessage",
                "params": MessageToDict(_user_request()),
            },
        )

        assert reply["result"]["statusUpdate"]["status"]["state"] == (
            "TASK_STATE_COMPLETED"
        )


class TestPatternsBridgeIdentityAuth:
    @staticmethod
    def _server_with_auth(authorize_ok: bool) -> A2AExperimentalServer:
        from agntcy_app_sdk.semantic.a2a.server import experimental_patterns as ep

        server = A2AExperimentalServer()
        server.bind_server(_config())
        sdk = MagicMock()
        if not authorize_ok:
            sdk.authorize.side_effect = RuntimeError("bad token")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ep, "IdentityServiceSdk", lambda: sdk)
            server._configure_identity_auth()
        return server

    @staticmethod
    def _request(headers: dict) -> Message:
        return Message(
            type="A2ARequest",
            payload=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "method": "SendMessage",
                    "params": MessageToDict(_user_request()),
                }
            ).encode(),
            headers=headers,
        )

    def test_configure_stamps_bearer_scheme_on_card(self):
        server = self._server_with_auth(True)
        card = server._server.agent_card  # type: ignore[union-attr]

        scheme = card.security_schemes["IdentityServiceAuthScheme"]
        assert scheme.http_auth_security_scheme.scheme == "bearer"
        assert scheme.http_auth_security_scheme.bearer_format == "JWT"
        assert len(card.security_requirements) == 1

    @pytest.mark.asyncio
    async def test_missing_authorization_header_is_rejected(self):
        server = self._server_with_auth(True)

        reply = await server.handle_message(self._request({}))

        error = json.loads(reply.payload)["error"]
        assert "Authorization" in error["data"] or "Authorization" in str(error)

    @pytest.mark.asyncio
    async def test_invalid_token_is_rejected(self):
        server = self._server_with_auth(False)

        reply = await server.handle_message(
            self._request({"Authorization": "Bearer nope"})
        )

        assert "error" in json.loads(reply.payload)

    @pytest.mark.asyncio
    async def test_valid_token_is_accepted(self):
        server = self._server_with_auth(True)

        reply = await server.handle_message(
            self._request({"Authorization": "Bearer ok"})
        )

        assert "error" not in json.loads(reply.payload)


class TestJsonRpcPublicUrl:
    """HTTP JSON-RPC serving: how the card learns its public JSONRPC URL."""

    @staticmethod
    def _handler(card: AgentCard, public_url: str | None = None):
        from agntcy_app_sdk.semantic.a2a.server.jsonrpc import A2AJsonRpcServerHandler

        return A2AJsonRpcServerHandler(
            _config(card), host="0.0.0.0", port=9000, public_url=public_url
        )

    @staticmethod
    def _pairs(card: AgentCard) -> list[tuple[str, str]]:
        return [(i.protocol_binding, i.url) for i in card.supported_interfaces]

    def test_public_url_is_appended_when_card_has_no_jsonrpc(self):
        card = _card()

        self._handler(card, "https://agent.example.com")._declare_jsonrpc_interface()

        assert self._pairs(card) == [("JSONRPC", "https://agent.example.com")]

    def test_public_url_is_appended_never_prepended(self):
        """Other transports (and the author's order) stay preferred."""
        card = _card([AgentInterface(protocol_binding="slimpatterns", url="slim://a")])

        self._handler(card, "https://agent.example.com")._declare_jsonrpc_interface()

        assert self._pairs(card) == [
            ("slimpatterns", "slim://a"),
            ("JSONRPC", "https://agent.example.com"),
        ]
        assert preferred_transport(card) == "slimpatterns"

    def test_declared_jsonrpc_interface_is_left_untouched(self):
        card = _card(
            [
                AgentInterface(
                    protocol_binding="JSONRPC", url="https://declared.example"
                ),
                AgentInterface(protocol_binding="slimpatterns", url="slim://a"),
            ]
        )

        self._handler(card, "https://other.example")._declare_jsonrpc_interface()

        assert self._pairs(card) == [
            ("JSONRPC", "https://declared.example"),
            ("slimpatterns", "slim://a"),
        ]

    def test_bind_address_is_never_written_to_the_card(self):
        card = _card()

        self._handler(card)._declare_jsonrpc_interface()

        assert self._pairs(card) == []

    def test_warns_when_no_interface_and_no_public_url(self):
        from agntcy_app_sdk.semantic.a2a.server import jsonrpc

        with pytest.MonkeyPatch.context() as mp:
            warning = MagicMock()
            mp.setattr(jsonrpc.logger, "warning", warning)
            self._handler(_card())._declare_jsonrpc_interface()

        warning.assert_called_once()
        assert "with_public_url" in warning.call_args.args[0]

    def test_no_warning_when_public_url_given(self):
        from agntcy_app_sdk.semantic.a2a.server import jsonrpc

        with pytest.MonkeyPatch.context() as mp:
            warning = MagicMock()
            mp.setattr(jsonrpc.logger, "warning", warning)
            self._handler(_card(), "https://a.example")._declare_jsonrpc_interface()

        warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_served_agent_card_includes_public_url(self):
        """End to end: the HTTP-served card advertises the appended interface."""
        card = _card()
        handler = self._handler(card, "https://agent.example.com")
        handler._declare_jsonrpc_interface()

        body = (
            TestClient(handler._server.build_app())
            .get("/.well-known/agent-card.json")
            .json()
        )

        assert len(body["supportedInterfaces"]) == 1
        served = body["supportedInterfaces"][0]
        assert served["protocolBinding"] == "JSONRPC"
        assert served["url"] == "https://agent.example.com"


class TestBuilderPublicUrl:
    @staticmethod
    def _session():
        from agntcy_app_sdk.factory import AgntcyFactory

        return AgntcyFactory().create_app_session(max_sessions=1)

    def test_with_public_url_reaches_the_http_handler(self):
        session = self._session()

        container = (
            session.add(_config())
            .with_host("0.0.0.0")
            .with_port(9111)
            .with_public_url("https://agent.example.com")
            .build()
        )

        assert container.handler._public_url == "https://agent.example.com"

    @pytest.mark.parametrize("url", ["agent.example.com", "slim://x", "ftp://x"])
    def test_with_public_url_rejects_non_http_urls(self, url):
        builder = self._session().add(_config())

        with pytest.raises(ValueError, match="http"):
            builder.with_public_url(url)

    def test_public_url_is_ignored_with_a_transport(self):
        from agntcy_app_sdk import app_sessions

        session = self._session()
        warning = MagicMock()

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(app_sessions.logger, "warning", warning)
            session.add(_config()).with_transport(
                _mock_transport("SLIM")
            ).with_public_url("https://agent.example.com").build()

        assert any("public_url" in c.args[0] for c in warning.call_args_list)


class TestClientErrorForEmptyCard:
    @pytest.mark.asyncio
    async def test_create_with_no_interfaces_explains_the_fix(self):
        factory = A2AClientFactory(ClientConfig())

        with pytest.raises(ValueError, match="No compatible transports") as exc:
            await factory.create(_card())

        assert "supported_interfaces" in str(exc.value)
        assert "connect(url)" in str(exc.value)
