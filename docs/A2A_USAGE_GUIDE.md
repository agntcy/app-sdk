# A2A Usage Guide

In this guide, we will walk through the key features of the Agntcy Application SDK's A2A (Agent-to-Agent) integration and explore end-to-end examples of creating agents that communicate over different transports.

## Architecture

The SDK decouples _protocols_ (the semantic layer — how agents talk) from _transports_ (the delivery layer — how bytes move). For A2A, three server handler paths exist depending on the transport:

```
                            AgntcyFactory
                           /      |       \
                     .a2a()  .create_transport()  .create_app_session()
                       |           |                     |
              A2AClientFactory  BaseTransport         AppSession
                       |      (SLIM / NATS / HTTP)       |
                       |           |               .add(target)
                       v           |                     |
                    Client         |            ContainerBuilder
                                   |          .with_transport(t)
                                   |          .with_session_id(id)
                                   |                     |
                                   |                  .build()
                                   |                 /   |   \
                                  v                v     v     v
                                         ┌──────────────────────────┐
                                         │   Handler auto-detection │
                                         └──────────┬───────────────┘
                        ┌─────────────────┬──────────┴──────────┐
                        v                 v                      v
              A2ASRPCServerHandler   A2AJsonRpc           A2AExperimental
              (A2ASRPCConfig)        ServerHandler         ServerHandler
                   │                (A2AStarlette          (A2AStarlette
                   │                 Application,           Application
                   │                 no transport)         + transport)
                   │                      │                     │
                   v                      v                     v
             slim_bindings           Uvicorn/ASGI          JSONRPCHandler
             .Server (RPC)          (HTTP JSONRPC)        (direct dispatch)
                   │                      │                     │
                   v                      v                     v
              SLIM gateway           HTTP clients          SLIM / NATS
              (protobuf)             (standard A2A)        (patterns)
```

**Handler auto-detection** — When you call `session.add(target).build()`, the SDK inspects the `target` type:

| Target type               | Transport provided? | Handler selected                                                                             |
| ------------------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| `A2ASRPCConfig`           | _(ignored)_         | `A2ASRPCServerHandler` — native SLIM RPC via `slim_bindings.Server`                          |
| `A2AStarletteApplication` | No                  | `A2AJsonRpcServerHandler` — serves over HTTP via Uvicorn                                     |
| `A2AStarletteApplication` | Yes                 | `A2AExperimentalServerHandler` — routes transport messages directly through `JSONRPCHandler` |

The following table summarizes current A2A transport support:

| Handler \ Transport                                        | SLIM | NATS | HTTP |
| ---------------------------------------------------------- | :--: | :--: | :--: |
| **SlimRPC** (`A2ASRPCServerHandler`)                       |  ✅  |  —   |  —   |
| **JSONRPC** (`A2AJsonRpcServerHandler`)                    |  —   |  —   |  ✅  |
| **Experimental Patterns** (`A2AExperimentalServerHandler`) |  ✅  |  ✅  |  —   |

---

## Setup

We will use `uv` for package management and virtual environments. If you don't have it installed, you can install it via:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a new project directory:

```bash
uv init agntcy-a2a
cd agntcy-a2a
```

Install the Agntcy Application SDK:

```bash
uv add agntcy-app-sdk
```

---

## Example 1 — SlimRPC (Native SLIM RPC)

SlimRPC is the recommended path for agents communicating over SLIM. It uses the native `slim_bindings.Server` with protobuf-serialized A2A messages — no HTTP layer involved.

### Server: `weather_agent_srpc.py`

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASRPCConfig
from agntcy_app_sdk.factory import AgntcyFactory

"""
Define the AgentSkill and AgentCard.
"""

skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)

agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    url="",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supportsAuthenticatedExtendedCard=False,
)

"""
Implement the agent logic and executor.
"""

class WeatherAgent:
    """A simple agent that returns a weather report."""
    async def invoke(self) -> str:
        return "The weather is sunny with a high of 75F."

class WeatherAgentExecutor(AgentExecutor):
    def __init__(self):
        self.agent = WeatherAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        result = await self.agent.invoke()
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

"""
Create an A2ASRPCConfig and serve via AppSession.
"""

async def main():
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    srpc_config = A2ASRPCConfig(
        agent_card=agent_card,
        request_handler=request_handler,
        slimrpc_server_config={
            "identity": "default/default/weather-agent",
            "slim_client_config": {"endpoint": "http://localhost:46357"},
            "shared_secret": "my-shared-secret",
        },
    )

    session = factory.create_app_session(max_sessions=1)
    session.add(srpc_config).with_session_id("weather").build()

    await session.start_all_sessions(keep_alive=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Client: `weather_client_srpc.py`

The client uses the SDK's `ClientConfig` to declare which transports it supports. The factory negotiates the best match with the server's `AgentCard` at connect time — so you can configure multiple transports on a single config and the right one is selected automatically.

```python
from a2a.client import minimal_agent_card
from a2a.types import Message, Part, Role, TextPart
from slima2a import setup_slim_client
from slima2a.client_transport import slimrpc_channel_factory

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

async def main():
    factory = AgntcyFactory()

    # 1. Set up the low-level SLIM connection (needed for slimrpc channel)
    _service, slim_app, _local_name, conn_id = await setup_slim_client(
        namespace="default",
        group="default",
        name="weather_client",
        slim_url="http://localhost:46357",
    )

    # 2. Build a ClientConfig with slimrpc enabled.
    #    ClientConfig can hold multiple transport configs simultaneously
    #    (slimrpc, slimpatterns, natspatterns, JSONRPC) — the factory
    #    negotiates the best match against the server's AgentCard.
    config = ClientConfig(
        slimrpc_channel_factory=slimrpc_channel_factory(slim_app, conn_id),
        # You could also add other transports here, e.g.:
        # slim_transport=my_slim_transport,      # for experimental patterns
        # nats_config=NatsTransportConfig(...),   # for NATS patterns
    )

    # 3. Create client — transport negotiation happens inside create()
    card = minimal_agent_card("default/default/weather-agent", ["slimrpc"])
    client = await factory.a2a(config).create(card)

    # 4. Send a message
    request = Message(
        role=Role.user,
        message_id="msg-001",
        parts=[Part(root=TextPart(text="Hello, Weather Agent, how is the weather?"))],
    )
    async for event in client.send_message(request=request):
        if isinstance(event, Message):
            for part in event.parts:
                if isinstance(part.root, TextPart):
                    print(part.root.text)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

A few notes:

- **Server:** `A2ASRPCConfig` bundles the agent card, request handler, and SLIM connection config into a single object — the handler auto-detection in `session.add(srpc_config).build()` selects `A2ASRPCServerHandler` automatically. SlimRPC manages its own transport internally, so you do **not** call `.with_transport()`.
- **Client:** The SDK's `ClientConfig` declares all transports the client is capable of using. The `slimrpc_channel_factory` field enables slimrpc; `supported_transports` is auto-derived in `__post_init__` from whichever fields are populated. When `factory.a2a(config).create(card)` is called, the factory negotiates the best transport match between the config and the agent card.
- **Multi-transport:** A single `ClientConfig` can hold slimrpc, slimpatterns, natspatterns, and JSONRPC configurations simultaneously. The factory picks the best match at connect time, so the same client config can talk to agents on different transports.

### Running

First start the SLIM transport server — see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/services/docker/docker-compose.yaml) or SLIM [repo](https://github.com/agntcy/slim/tree/main).

```bash
uv run python weather_agent_srpc.py
```

You should see:

```
[agntcy_app_sdk.semantic.a2a.server.srpc] [INFO] slimrpc A2A handler started for identity 'default/default/weather-agent'
[agntcy_app_sdk.app_sessions] [INFO] App started. Waiting for shutdown signal (Ctrl+C)...
```

In another terminal:

```bash
uv run python weather_client_srpc.py
```

---

## Example 2 — Experimental Patterns (Broadcast / Group Chat over SLIM | NATS)

The experimental patterns path enables communication patterns beyond the A2A spec — broadcast (publish/subscribe) and multi-party group chat — over SLIM or NATS transports. It preserves A2A's AgentCard handshake, JSON-RPC envelope, and typed payloads while routing transport messages directly through the `JSONRPCHandler` (bypassing Starlette/ASGI entirely).

### Server: `weather_agent.py`

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import A2AExperimentalServer
from agntcy_app_sdk.factory import AgntcyFactory

"""
Define the AgentSkill and AgentCard.
"""

skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)

agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    url="",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supportsAuthenticatedExtendedCard=False,
)

"""
Implement the agent logic and executor.
"""

class WeatherAgent:
    """A simple agent that returns a weather report."""
    async def invoke(self) -> str:
        return "The weather is sunny with a high of 75F."

class WeatherAgentExecutor(AgentExecutor):
    def __init__(self):
        self.agent = WeatherAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        result = await self.agent.invoke()
        await event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

"""
Create the A2A server and serve via AppSession with a SLIM transport.
"""

async def main():
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    topic = A2AExperimentalServer.create_agent_topic(agent_card)
    name = f"default/default/{topic}"
    transport = factory.create_transport("SLIM", endpoint="http://localhost:46357", name=name)

    session = factory.create_app_session(max_sessions=1)
    session.add(server).with_transport(transport).with_session_id("weather").build()

    await session.start_all_sessions(keep_alive=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Client: `weather_client.py`

```python
from a2a.types import (
    SendMessageRequest,
    MessageSendParams,
    Message,
    Part,
    TextPart,
    Role,
)

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import A2AExperimentalServer
from weather_agent import agent_card

async def main():
    factory = AgntcyFactory()

    transport = factory.create_transport(
        "SLIM",
        endpoint="http://localhost:46357",
        name="default/default/weather_client",
    )

    card = A2AExperimentalServer.create_client_card(agent_card, "SLIM")

    config = ClientConfig(slim_transport=transport)
    client = await factory.a2a(config).create(card)

    request = SendMessageRequest(
        id="request-001",
        params=MessageSendParams(
            message=Message(
                messageId="0",
                role=Role.user,
                parts=[Part(TextPart(text="Hello, Weather Agent, how is the weather?"))],
            ),
        ),
    )

    response = await client.send_message(request)
    print(response)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

A few notes:

- The weather agent does not provide a URL in its agent card — it is discovered by the transport topic derived from the card.
- The client uses `A2AExperimentalServer.create_client_card` to build a card copy with the correct `preferred_transport` and `url` for transport negotiation.
- Passing a transport via `.with_transport()` triggers the experimental patterns handler; omitting it would serve over HTTP instead.

### Running

First start the SLIM transport server — see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/services/docker/docker-compose.yaml) or SLIM [repo](https://github.com/agntcy/slim/tree/main).

Run the weather agent server:

```bash
uv run python weather_agent.py
```

You should see:

```
...
[agntcy_app_sdk.app_sessions] [INFO] App started. Waiting for shutdown signal (Ctrl+C)...
```

In another terminal, run the weather client:

```bash
uv run python weather_client.py
```

You should see:

```
root=SendMessageSuccessResponse(id='...', jsonrpc='2.0', result=Message(..., parts=[Part(root=TextPart(kind='text', ..., text='The weather is sunny with a high of 75F.'))], ..., role=<Role.agent: 'agent'>, ...))
```

---

## Identity TBAC Integration

Activate Agntcy Identity Service TBAC by configuring the `IDENTITY_AUTH_ENABLED` and `IDENTITY_SERVICE_API_KEY` environment variable with the Identity App Service API key.
For more details, refer to the [official documentation](https://identity-docs.outshift.com/docs/dev#a2a-integration-using-the-python-sdk).

**Important**: Ensure the `IDENTITY_SERVICE_API_KEY` values for the client and server are different to enforce proper TBAC functionality.

---

## Contributing additional Transports

To contribute a new transport implementation, follow these steps:

1. **Implement the Transport Interface**: Create a new class for your transport in the `src/agntcy_app_sdk/transport/` directory. Ensure it inherits from the `BaseTransport` interface and implements all required methods.

2. **Update the Factory**: Modify the `AgntcyFactory` to include your new transport in the `create_transport` method.

3. **Add Tests**: Create unit tests for your transport in the `tests/e2e` directory. Ensure all tests pass.

4. **Documentation**: Update the documentation to include your new transport. This includes any relevant sections in the README and API reference.

5. **Submit a Pull Request**: Once your changes are complete, submit a pull request for review.

See [API Reference](API_REFERENCE.md) for detailed SDK API documentation.

For a fully functional multi-agent example integrating A2A, Agntcy, and Langgraph, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy).
