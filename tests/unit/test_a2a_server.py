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
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from a2a.client.client import ClientCallContext
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.server.context import ServerCallContext
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
from agntcy_app_sdk.semantic.a2a.transport_types import SENDER_HEADER
from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.base import BaseTransport
from tests.server.agent_executor import (
    HelloWorldAgentExecutor,
    HelloWorldStreamingAgentExecutor,
)

if TYPE_CHECKING:
    from agntcy_app_sdk.app_sessions import AppSession
    from agntcy_app_sdk.semantic.a2a.server.jsonrpc import A2AJsonRpcServerHandler

pytest_plugins = "pytest_asyncio"


@pytest_asyncio.fixture(autouse=True)
async def _cancel_background_tasks() -> AsyncIterator[None]:
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
    def test_holds_card_and_handler(self) -> None:
        config = _config()
        assert config.agent_card.name == "Hello World Agent"
        assert config.request_handler is not None
        assert config.rpc_url == "/"
        assert config.enable_v0_3_compat is False

    def test_is_exported_from_package_roots(self) -> None:
        import agntcy_app_sdk
        import agntcy_app_sdk.semantic.a2a as a2a_pkg

        assert agntcy_app_sdk.A2AServerConfig is A2AServerConfig
        assert a2a_pkg.A2AServerConfig is A2AServerConfig

    def test_build_app_serves_agent_card(self) -> None:
        card = _card([AgentInterface(protocol_binding="JSONRPC", url="http://h:1/")])
        app = _config(card).build_app()

        response = TestClient(app).get("/.well-known/agent-card.json")

        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Hello World Agent"
        assert body["supportedInterfaces"][0]["protocolBinding"] == "JSONRPC"

    def test_build_app_serves_jsonrpc_send_message(self) -> None:
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
    async def test_card_modifier_is_applied_to_served_card(self) -> None:
        async def modifier(card: AgentCard) -> AgentCard:
            card.description = "modified"
            return card

        config = _config()
        config.card_modifier = modifier

        response = TestClient(config.build_app()).get("/.well-known/agent-card.json")

        assert response.json()["description"] == "modified"


class TestDeprecatedA2AStarletteApplication:
    def test_emits_deprecation_warning(self) -> None:
        config = _config()
        with pytest.warns(DeprecationWarning, match="A2AServerConfig"):
            A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

    def test_behaves_as_server_config(self) -> None:
        config = _config()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

        assert isinstance(shim, A2AServerConfig)
        assert shim.agent_card is config.agent_card
        assert shim.request_handler is config.request_handler

    def test_legacy_attribute_and_method_aliases(self) -> None:
        config = _config()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            shim = A2AStarletteApplication(
                agent_card=config.agent_card, http_handler=config.request_handler
            )

        assert shim.http_handler is config.request_handler
        response = TestClient(shim.build()).get("/.well-known/agent-card.json")
        assert response.status_code == 200

    def test_session_accepts_the_shim(self) -> None:
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
    async def test_handler_declares_missing_patterns_interface_as_preferred(
        self,
    ) -> None:
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
    async def test_handler_leaves_author_declared_interface_untouched(self) -> None:
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
    async def test_nats_transport_declares_natspatterns(self) -> None:
        card = _card()
        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("NATS"), topic="t"
        )

        await handler.setup()

        assert [(i.protocol_binding, i.url) for i in card.supported_interfaces] == [
            ("natspatterns", "nats://t")
        ]

    def test_create_client_card_makes_patterns_interface_preferred(self) -> None:
        base = _card([AgentInterface(protocol_binding="JSONRPC", url="http://h:1/")])

        client_card = A2AExperimentalServer.create_client_card(
            base, "SLIM", topic="my_topic"
        )

        assert preferred_transport(client_card) == "slimpatterns"
        assert client_card.supported_interfaces[0].url == "slim://my_topic"
        # the base card is not mutated
        assert [i.protocol_binding for i in base.supported_interfaces] == ["JSONRPC"]

    def test_create_client_card_replaces_declared_url(self) -> None:
        base = _card(
            [AgentInterface(protocol_binding="slimpatterns", url="slim://old")]
        )

        client_card = A2AExperimentalServer.create_client_card(
            base, "SLIM", topic="new"
        )

        assert [
            (i.protocol_binding, i.url) for i in client_card.supported_interfaces
        ] == [("slimpatterns", "slim://new")]

    def test_create_client_card_derives_topic_from_card_name(self) -> None:
        client_card = A2AExperimentalServer.create_client_card(_card(), "NATS")

        assert preferred_transport(client_card) == "natspatterns"
        assert client_card.supported_interfaces[0].url.startswith("nats://")


# ---------------------------------------------------------------------------
# Default topic must come from the handler's own pub/sub interface
# ---------------------------------------------------------------------------

_RPC_FIRST_CARD_INTERFACES = [
    AgentInterface(protocol_binding="slimrpc", url="slim://org/ns/rpc_agent"),
    AgentInterface(protocol_binding="slimpatterns", url="slim://org/ns/pubsub_agent"),
]


class TestDefaultTopicSelection:
    """``slimrpc`` also uses ``slim://`` URLs, so a card whose first interface
    is another transport must never supply the topic for a pub/sub handler
    (it would collide with the RPC server's name)."""

    def test_handler_ignores_leading_slimrpc_interface(self) -> None:
        card = _card(list(_RPC_FIRST_CARD_INTERFACES))

        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM")
        )

        assert handler._topic == "org/ns/pubsub_agent"

    def test_handler_without_pubsub_interface_does_not_use_slimrpc_identity(
        self,
    ) -> None:
        card = _card(
            [AgentInterface(protocol_binding="slimrpc", url="slim://org/ns/rpc_agent")]
        )

        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM")
        )

        assert handler._topic == "Hello_World_Agent_1.0.0"

    def test_handler_picks_interface_matching_its_own_transport(self) -> None:
        card = _card(
            [
                AgentInterface(protocol_binding="natspatterns", url="nats://nats_t"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://slim_t"),
            ]
        )

        slim = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM")
        )
        nats = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("NATS")
        )

        assert slim._topic == "slim_t"
        assert nats._topic == "nats_t"

    def test_handler_without_transport_uses_preferred_interface(self) -> None:
        """Binding unknown without a transport: the first pub/sub interface wins."""
        card = _card(
            [AgentInterface(protocol_binding="slimpatterns", url="slim://first")]
        )

        handler = A2AExperimentalServerHandler(_config(card))

        assert handler._topic == "first"

    def test_handler_without_transport_skips_bare_slimrpc_identity(self) -> None:
        card = _card(
            [
                AgentInterface(protocol_binding="slimrpc", url="org/ns/rpc_agent"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://t"),
            ]
        )

        handler = A2AExperimentalServerHandler(_config(card))

        assert handler._topic == "t"

    def test_handler_without_transport_skips_slimrpc_url_identity(self) -> None:
        card = _card(list(_RPC_FIRST_CARD_INTERFACES))

        handler = A2AExperimentalServerHandler(_config(card))

        assert handler._topic == "org/ns/pubsub_agent"

    def test_handler_without_transport_only_slimrpc_falls_back_to_name(
        self,
    ) -> None:
        card = _card(
            [AgentInterface(protocol_binding="slimrpc", url="org/ns/rpc_agent")]
        )

        handler = A2AExperimentalServerHandler(_config(card))

        assert handler._topic == "Hello_World_Agent_1.0.0"

    def test_handler_without_transport_uses_first_pubsub_interface_in_card_order(
        self,
    ) -> None:
        card = _card(
            [
                AgentInterface(protocol_binding="JSONRPC", url="http://h:1/"),
                AgentInterface(protocol_binding="natspatterns", url="nats://n"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://s"),
            ]
        )

        handler = A2AExperimentalServerHandler(_config(card))

        assert handler._topic == "n"

    def test_handler_with_unmapped_transport_skips_slimrpc_identity(self) -> None:
        card = _card(
            [
                AgentInterface(protocol_binding="slimrpc", url="org/ns/rpc_agent"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://t"),
            ]
        )

        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("STREAMABLE_HTTP")
        )

        assert handler._topic == "t"
        assert handler._protocol._sender_id == "t"

    def test_handler_explicit_topic_is_not_overridden(self) -> None:
        card = _card(list(_RPC_FIRST_CARD_INTERFACES))

        handler = A2AExperimentalServerHandler(
            _config(card), transport=_mock_transport("SLIM"), topic="explicit"
        )

        assert handler._topic == "explicit"

    def test_create_transport_uri_ignores_leading_slimrpc_interface(self) -> None:
        card = _card(list(_RPC_FIRST_CARD_INTERFACES))

        uri = A2AExperimentalServer.create_transport_uri(card, "SLIM")

        assert uri == "slim://org/ns/pubsub_agent"

    def test_create_transport_uri_without_pubsub_interface_falls_back_to_name(
        self,
    ) -> None:
        card = _card(
            [AgentInterface(protocol_binding="slimrpc", url="slim://org/ns/rpc_agent")]
        )

        uri = A2AExperimentalServer.create_transport_uri(card, "SLIM")

        assert uri == "slim://Hello_World_Agent_1.0.0"

    def test_create_transport_uri_picks_interface_matching_transport(self) -> None:
        card = _card(
            [
                AgentInterface(protocol_binding="natspatterns", url="nats://nats_t"),
                AgentInterface(protocol_binding="slimpatterns", url="slim://slim_t"),
            ]
        )

        assert (
            A2AExperimentalServer.create_transport_uri(card, "SLIM") == "slim://slim_t"
        )
        assert (
            A2AExperimentalServer.create_transport_uri(card, "NATS") == "nats://nats_t"
        )

    def test_create_client_card_ignores_leading_slimrpc_interface(self) -> None:
        card = _card(list(_RPC_FIRST_CARD_INTERFACES))

        client_card = A2AExperimentalServer.create_client_card(card, "SLIM")

        assert preferred_transport(client_card) == "slimpatterns"
        assert client_card.supported_interfaces[0].url == "slim://org/ns/pubsub_agent"


# ---------------------------------------------------------------------------
# Patterns bridge over an in-memory loopback transport
# ---------------------------------------------------------------------------


class _LoopbackTransport:
    """Minimal ``BaseTransport`` stand-in wiring client calls straight into an
    ``A2AExperimentalServer`` — the same path SLIM / NATS would carry."""

    gather_stream: Callable[..., AsyncIterator[Message]]

    def __init__(self, server: A2AExperimentalServer) -> None:
        self._server = server
        self.sent: list[Message] = []

    def type(self) -> str:
        return "SLIM"

    async def setup(self) -> None:  # pragma: no cover - trivial
        pass

    async def close(self) -> None:  # pragma: no cover - trivial
        pass

    async def request(self, topic: str, message: Message, **_kwargs: Any) -> Message:
        self.sent.append(message)
        return await self._server.handle_message(message)

    async def request_stream(
        self, topic: str, message: Message, **_kwargs: Any
    ) -> AsyncIterator[Message]:
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


async def _loopback_client(
    streaming: bool,
) -> tuple[A2AExperimentalClient, _LoopbackTransport]:
    _, transport = _loopback(streaming)
    card = _card(
        [AgentInterface(protocol_binding="slimpatterns", url="slim://agent")],
        streaming=streaming,
    )
    factory = A2AClientFactory(
        ClientConfig(slim_transport=cast(BaseTransport, transport), streaming=streaming)
    )
    client = await factory.create(card)
    assert isinstance(client, A2AExperimentalClient)
    return client, transport


class TestPatternsBridgeLoopback:
    @pytest.mark.asyncio
    async def test_unary_send_message_round_trip(self) -> None:
        client, _ = await _loopback_client(streaming=False)

        events = [e async for e in client.send_message(_user_request())]

        assert len(events) == 1
        assert events[0].HasField("message")
        assert get_stream_response_text(events[0]) == "Hello from agent"

    @pytest.mark.asyncio
    async def test_streaming_send_message_yields_task_then_status_updates(self) -> None:
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
    async def test_get_task_after_streaming_run(self) -> None:
        client, _ = await _loopback_client(streaming=True)
        events = [e async for e in client.send_message(_user_request())]
        task_id = events[0].task.id

        task = await client.get_task(GetTaskRequest(id=task_id))

        assert task.id == task_id
        assert task.status.state == TaskState.TASK_STATE_COMPLETED

    @pytest.mark.asyncio
    async def test_unknown_task_raises_task_not_found(self) -> None:
        client, _ = await _loopback_client(streaming=False)

        with pytest.raises(TaskNotFoundError):
            await client.get_task(GetTaskRequest(id="does-not-exist"))

    @pytest.mark.asyncio
    async def test_request_headers_reach_the_server_context(self) -> None:
        server, transport = _loopback(streaming=False)
        seen: dict[str, Any] = {}
        assert server._handler is not None
        original = server._handler.on_message_send

        async def _spy(request: SendMessageRequest, context: ServerCallContext) -> Any:
            seen.update(context.state["headers"])
            seen["method"] = context.state["method"]
            return await original(request, context)

        server._handler.on_message_send = _spy
        pct = PatternsClientTransport(
            cast(BaseTransport, transport),
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
    async def test_broadcast_message_round_trip(self) -> None:
        client, transport = await _loopback_client(streaming=False)

        async def _gather_stream(
            topic: str, message: Message, **_kwargs: Any
        ) -> AsyncIterator[Message]:
            yield await transport.request(topic, message)

        transport.gather_stream = _gather_stream

        responses = await client.broadcast_message(_user_request(), recipients=["x"])

        assert len(responses) == 1
        assert responses[0].message.parts[0].text == "Hello from agent"


class TestPatternsBridgeProtocol:
    """Direct ``handle_message`` checks: JSON-RPC envelope handling."""

    @staticmethod
    async def _call(
        server: A2AExperimentalServer, body: bytes | dict[str, Any]
    ) -> dict[str, Any]:
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        reply = await server.handle_message(
            Message(type="A2ARequest", payload=payload, headers={})
        )
        assert reply.type == "A2AResponse"
        rpc: dict[str, Any] = json.loads(reply.payload)
        return rpc

    @pytest.mark.asyncio
    async def test_invalid_json_is_a_parse_error(self) -> None:
        server, _ = _loopback(streaming=False)
        reply = await self._call(server, b"{not json")
        assert reply["error"]["code"] == -32700

    @pytest.mark.asyncio
    async def test_missing_method_is_invalid_request(self) -> None:
        server, _ = _loopback(streaming=False)
        reply = await self._call(server, {"jsonrpc": "2.0", "id": "1"})
        assert reply["error"]["code"] == -32600

    @pytest.mark.asyncio
    async def test_unknown_method_is_method_not_found(self) -> None:
        server, _ = _loopback(streaming=False)
        reply = await self._call(
            server, {"jsonrpc": "2.0", "id": "1", "method": "message/send"}
        )
        assert reply["error"]["code"] == -32601
        assert reply["id"] == "1"

    @pytest.mark.asyncio
    async def test_invalid_params_is_invalid_params(self) -> None:
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
    async def test_relayed_response_is_rewrapped_as_send_message(self) -> None:
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

    @staticmethod
    def _agent_message(text: str, **ids: str) -> dict[str, Any]:
        return {
            "messageId": f"m-{text}",
            "role": "ROLE_AGENT",
            "parts": [{"text": text}],
            **ids,
        }

    @staticmethod
    def _relayed_task(task: dict[str, Any]) -> bytes:
        return json.dumps(
            {"jsonrpc": "2.0", "id": "upstream", "result": {"task": task}}
        ).encode()

    @pytest.mark.asyncio
    async def test_relayed_task_forwards_status_message(self) -> None:
        server, _ = _loopback(streaming=False)
        task = {
            "id": "t-up",
            "contextId": "c-up",
            "status": {
                "state": "TASK_STATE_COMPLETED",
                "message": self._agent_message("status", taskId="t-up"),
            },
            "history": [self._agent_message("older", taskId="t-up")],
        }

        reply = await self._call(server, self._relayed_task(task))

        assert "error" not in reply
        assert reply["result"]["message"]["parts"][0]["text"] == "Hello from agent"

    def test_relayed_task_without_status_message_uses_last_agent_message(
        self,
    ) -> None:
        task = {
            "id": "t-up",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "history": [
                self._agent_message("first"),
                self._agent_message("last", taskId="t-up", contextId="c-up"),
                {"messageId": "m-user", "role": "ROLE_USER", "parts": [{"text": "q"}]},
            ],
        }

        request = json.loads(
            A2AExperimentalServer._unwrap_relay(self._relayed_task(task))
        )

        assert request["method"] == "SendMessage"
        assert request["params"]["message"] == {
            "messageId": "m-last",
            "role": "ROLE_AGENT",
            "parts": [{"text": "last"}],
            "contextId": "c-up",
            "metadata": {"relayed_from": {"task_id": "t-up", "context_id": "c-up"}},
        }

    @pytest.mark.asyncio
    async def test_relayed_task_with_artifacts_only_is_not_relayed(self) -> None:
        server, _ = _loopback(streaming=False)
        task = {
            "id": "t-up",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"artifactId": "a1", "parts": [{"text": "file"}]}],
        }

        reply = await self._call(server, self._relayed_task(task))

        assert reply["error"]["code"] == -32600
        assert "Task with no agent message" in reply["error"]["data"]

    @pytest.mark.asyncio
    async def test_relayed_message_with_upstream_task_id_moves_it_to_metadata(
        self,
    ) -> None:
        server, _ = _loopback(streaming=False)
        msg = self._agent_message("hi", taskId="t-up")
        msg["metadata"] = {"trace": "keep"}
        relayed = json.dumps(
            {"jsonrpc": "2.0", "id": "upstream", "result": {"message": msg}}
        ).encode()

        request = json.loads(A2AExperimentalServer._unwrap_relay(relayed))
        reply = await self._call(server, relayed)

        assert "taskId" not in request["params"]["message"]
        assert request["params"]["message"]["metadata"] == {
            "trace": "keep",
            "relayed_from": {"task_id": "t-up"},
        }
        assert "error" not in reply

    @pytest.mark.asyncio
    async def test_streaming_publishes_intermediates_then_final(self) -> None:
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
    async def test_streaming_without_publish_fn_returns_last_item(self) -> None:
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

    @staticmethod
    def _send(method: str = "SendMessage") -> Message:
        return Message(
            type="A2ARequest",
            payload=json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": "h1",
                    "method": method,
                    "params": MessageToDict(_user_request()),
                }
            ).encode(),
            headers={},
        )

    @pytest.mark.asyncio
    async def test_reply_carries_sender_header(self) -> None:
        server = A2AExperimentalServer(sender_id="org/ns/agent_a")
        server.bind_server(_config())

        reply = await server.handle_message(self._send())

        assert reply.headers[SENDER_HEADER] == "org/ns/agent_a"

    @pytest.mark.asyncio
    async def test_error_reply_carries_sender_header(self) -> None:
        server = A2AExperimentalServer(sender_id="agent_a")
        server.bind_server(_config())

        reply = await server.handle_message(
            Message(type="A2ARequest", payload=b"{not json", headers={})
        )

        assert reply.headers[SENDER_HEADER] == "agent_a"

    @pytest.mark.asyncio
    async def test_streaming_replies_carry_sender_header(self) -> None:
        server = A2AExperimentalServer(sender_id="agent_s")
        server.bind_server(_config(streaming=True))
        published: list[Message] = []

        async def _publish(msg: Message) -> None:
            published.append(msg)

        reply = await server.handle_message(
            self._send("SendStreamingMessage"), publish_fn=_publish
        )

        assert published
        assert all(m.headers[SENDER_HEADER] == "agent_s" for m in published)
        assert reply.headers[SENDER_HEADER] == "agent_s"

    @pytest.mark.asyncio
    async def test_without_sender_id_no_header_is_added(self) -> None:
        server, _ = _loopback(streaming=False)

        reply = await server.handle_message(self._send())

        assert SENDER_HEADER not in (reply.headers or {})

    def test_handler_stamps_its_topic(self) -> None:
        handler = A2AExperimentalServerHandler(
            _config(), transport=_mock_transport("NATS"), topic="agent_t"
        )

        assert handler._protocol._sender_id == "agent_t"


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
    def _request(headers: dict[str, str]) -> Message:
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

    def test_configure_stamps_bearer_scheme_on_card(self) -> None:
        server = self._server_with_auth(True)
        assert server._server is not None
        card = server._server.agent_card

        scheme = card.security_schemes["IdentityServiceAuthScheme"]
        assert scheme.http_auth_security_scheme.scheme == "bearer"
        assert scheme.http_auth_security_scheme.bearer_format == "JWT"
        assert len(card.security_requirements) == 1

    @pytest.mark.asyncio
    async def test_missing_authorization_header_is_rejected(self) -> None:
        server = self._server_with_auth(True)

        reply = await server.handle_message(self._request({}))

        error = json.loads(reply.payload)["error"]
        assert "Authorization" in error["data"] or "Authorization" in str(error)

    @pytest.mark.asyncio
    async def test_invalid_token_is_rejected(self) -> None:
        server = self._server_with_auth(False)

        reply = await server.handle_message(
            self._request({"Authorization": "Bearer nope"})
        )

        assert "error" in json.loads(reply.payload)

    @pytest.mark.asyncio
    async def test_valid_token_is_accepted(self) -> None:
        server = self._server_with_auth(True)

        reply = await server.handle_message(
            self._request({"Authorization": "Bearer ok"})
        )

        assert "error" not in json.loads(reply.payload)


class TestJsonRpcPublicUrl:
    """HTTP JSON-RPC serving: how the card learns its public JSONRPC URL."""

    @staticmethod
    def _handler(
        card: AgentCard, public_url: str | None = None
    ) -> "A2AJsonRpcServerHandler":
        from agntcy_app_sdk.semantic.a2a.server.jsonrpc import A2AJsonRpcServerHandler

        return A2AJsonRpcServerHandler(
            _config(card), host="0.0.0.0", port=9000, public_url=public_url
        )

    @staticmethod
    def _pairs(card: AgentCard) -> list[tuple[str, str]]:
        return [(i.protocol_binding, i.url) for i in card.supported_interfaces]

    def test_public_url_is_appended_when_card_has_no_jsonrpc(self) -> None:
        card = _card()

        self._handler(card, "https://agent.example.com")._declare_jsonrpc_interface()

        assert self._pairs(card) == [("JSONRPC", "https://agent.example.com")]

    def test_public_url_is_appended_never_prepended(self) -> None:
        """Other transports (and the author's order) stay preferred."""
        card = _card([AgentInterface(protocol_binding="slimpatterns", url="slim://a")])

        self._handler(card, "https://agent.example.com")._declare_jsonrpc_interface()

        assert self._pairs(card) == [
            ("slimpatterns", "slim://a"),
            ("JSONRPC", "https://agent.example.com"),
        ]
        assert preferred_transport(card) == "slimpatterns"

    def test_declared_jsonrpc_interface_is_left_untouched(self) -> None:
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

    def test_bind_address_is_never_written_to_the_card(self) -> None:
        card = _card()

        self._handler(card)._declare_jsonrpc_interface()

        assert self._pairs(card) == []

    def test_warns_when_no_interface_and_no_public_url(self) -> None:
        from agntcy_app_sdk.semantic.a2a.server import jsonrpc

        with pytest.MonkeyPatch.context() as mp:
            warning = MagicMock()
            mp.setattr(jsonrpc.logger, "warning", warning)
            self._handler(_card())._declare_jsonrpc_interface()

        warning.assert_called_once()
        assert "with_public_url" in warning.call_args.args[0]

    def test_no_warning_when_public_url_given(self) -> None:
        from agntcy_app_sdk.semantic.a2a.server import jsonrpc

        with pytest.MonkeyPatch.context() as mp:
            warning = MagicMock()
            mp.setattr(jsonrpc.logger, "warning", warning)
            self._handler(_card(), "https://a.example")._declare_jsonrpc_interface()

        warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_served_agent_card_includes_public_url(self) -> None:
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
    def _session() -> "AppSession":
        from agntcy_app_sdk.factory import AgntcyFactory

        return AgntcyFactory().create_app_session(max_sessions=1)

    def test_with_public_url_reaches_the_http_handler(self) -> None:
        from agntcy_app_sdk.semantic.a2a.server.jsonrpc import A2AJsonRpcServerHandler

        session = self._session()

        container = (
            session.add(_config())
            .with_host("0.0.0.0")
            .with_port(9111)
            .with_public_url("https://agent.example.com")
            .build()
        )

        assert isinstance(container.handler, A2AJsonRpcServerHandler)
        assert container.handler._public_url == "https://agent.example.com"

    @pytest.mark.parametrize("url", ["agent.example.com", "slim://x", "ftp://x"])
    def test_with_public_url_rejects_non_http_urls(self, url: str) -> None:
        builder = self._session().add(_config())

        with pytest.raises(ValueError, match="http"):
            builder.with_public_url(url)

    def test_public_url_is_ignored_with_a_transport(self) -> None:
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
    async def test_create_with_no_interfaces_explains_the_fix(self) -> None:
        factory = A2AClientFactory(ClientConfig())

        with pytest.raises(ValueError, match="No compatible transports") as exc:
            await factory.create(_card())

        assert "supported_interfaces" in str(exc.value)
        assert "connect(url)" in str(exc.value)


def test_authenticate_without_identity_sdk_denies() -> None:
    server = A2AExperimentalServer(sender_id="agent")
    server._auth_enabled = True
    message = Message(
        type="request", payload=b"", headers={"Authorization": "Bearer token"}
    )

    ok, reason, _ = server._authenticate(message)

    assert not ok
    assert reason == "Identity service is not configured"
