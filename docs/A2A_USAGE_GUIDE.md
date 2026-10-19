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
                       |      (SLIM / NATS / HTTP)    /        \
                       |           |      .add(target)    .add_a2a_card(card, handler)
                       v           |           |                     |
                    Client         |   ContainerBuilder         CardBuilder
                                   |  .with_transport(t)     .with_factory(f)
                                   |  .with_session_id(id)   .skip() / .override()
                                   |           |                     |
                                   |        .build()              .start()
                                   |       /   |   \          (auto-expands card
                                  v       v    v    v          interfaces)
                                         ┌──────────────────────────┐
                                         │   Handler auto-detection │
                                         └──────────┬───────────────┘
                        ┌─────────────────┬──────────┴──────────┐
                        v                 v                      v
              A2ASRPCServerHandler   A2AJsonRpc           A2AExperimental
              (A2ASlimRpcServer-    ServerHandler         ServerHandler
               Config)             (A2AServerConfig,      (A2AServerConfig
                   │                 no transport)         + transport)
                   │                      │                     │
                   v                      v                     v
             slim_bindings           Uvicorn/ASGI          RequestHandler
             .Server (RPC)          (HTTP JSONRPC)        (direct dispatch)
                   │                      │                     │
                   v                      v                     v
              SLIM gateway           HTTP clients          SLIM / NATS
              (protobuf)             (standard A2A)        (patterns)
```

**Handler auto-detection** — When you call `session.add(target).build()` or `session.add_a2a_card(card, handler).start()`, the SDK inspects the target type:

| Target type               | Transport provided? | Handler selected                                                                             |
| ------------------------- | ------------------- | -------------------------------------------------------------------------------------------- |
| `A2ASlimRpcServerConfig`  | _(ignored)_         | `A2ASRPCServerHandler` — native SLIM RPC via `slim_bindings.Server`                          |
| `A2AServerConfig`         | No                  | `A2AJsonRpcServerHandler` — serves over HTTP via Uvicorn                                     |
| `A2AServerConfig`         | Yes                 | `A2AExperimentalServerHandler` — routes transport messages directly through the `RequestHandler` |

> **a2a-sdk 1.x:** the upstream `A2AStarletteApplication` was removed. `A2AServerConfig(agent_card, request_handler)` is its replacement: a plain description of what to serve that the SDK exposes over HTTP JSON-RPC, SLIM patterns, or NATS patterns. `A2AStarletteApplication` remains as a **deprecated** shim that emits a `DeprecationWarning`; see the [0.5.0 migration guide](0.5.0_MIGRATION.md) for the full migration notes.

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

Server and client must use the same SLIM shared secret (at least 32 characters). Both examples hardcode the same demo value on purpose.

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASlimRpcServerConfig, SlimRpcConnectionConfig
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
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
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
        await event_queue.enqueue_event(new_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

"""
Create an A2ASlimRpcServerConfig and serve via AppSession.
"""

async def main():
    factory = AgntcyFactory()

    # a2a-sdk 1.x: the handler needs the card at construction time
    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    srpc_config = A2ASlimRpcServerConfig(
        agent_card=agent_card,
        request_handler=request_handler,
        connection=SlimRpcConnectionConfig(
            identity="default/default/weather-agent",
            # Hardcoded on purpose: a demo value, not a real secret.
            shared_secret="my-shared-secret-at-least-32-characters-long",
            endpoint="http://localhost:46357",
        ),
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
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import Role, SendMessageRequest
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
        # Hardcoded on purpose: a demo value, not a real secret. Must match
        # the server's shared_secret.
        secret="my-shared-secret-at-least-32-characters-long",
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

    # 3. Create client — transport negotiation happens inside create().
    # With IDENTITY_AUTH_ENABLED, every slimrpc call carries this agent's
    # identity token, whatever the card declares (see Identity TBAC below).
    card = minimal_agent_card("default/default/weather-agent", ["slimrpc"])
    client = await factory.a2a(config).create(card)

    # 4. Send a message. Events are `StreamResponse` objects carrying a
    #    message, a task, or a status/artifact update.
    request = SendMessageRequest(
        message=new_text_message(
            "Hello, Weather Agent, how is the weather?", role=Role.ROLE_USER
        )
    )
    async for event in client.send_message(request):
        text = get_stream_response_text(event)
        if text:
            print(text)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

A few notes:

- **Server:** `A2ASlimRpcServerConfig` bundles the agent card, request handler, and SLIM connection config into a single object — the handler auto-detection in `session.add(srpc_config).build()` selects `A2ASRPCServerHandler` automatically. SlimRPC manages its own transport internally, so you do **not** call `.with_transport()`.
- **Client:** The SDK's `ClientConfig` declares all transports the client is capable of using. The `slimrpc_channel_factory` field enables slimrpc; `supported_protocol_bindings` is auto-derived in `__post_init__` from whichever fields are populated (the old `supported_transports` field is **deprecated** and still accepted). When `factory.a2a(config).create(card)` is called, the factory negotiates the best transport match between the config and the agent card.
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

## Example 2 — Card-Driven Multi-Transport Bootstrap (Recommended)

The **card-driven approach** is the recommended way to serve an A2A agent over multiple transports. Instead of manually creating transports and wiring builder chains, you declare all available transports in the agent card's `supported_interfaces` and let `add_a2a_card()` handle everything automatically.

This approach:

- Uses the **agent card as the single source of truth** for transport configuration
- Supports SLIM patterns, NATS patterns, HTTP JSON-RPC, and SlimRPC — all from one card
- Eliminates manual `create_transport()` / `with_transport()` / `build()` chains
- Still preserves A2A's AgentCard handshake, JSON-RPC envelope, and typed payloads

### Server: `weather_agent_card.py`

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.helpers import new_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

"""
Define the AgentSkill and AgentCard with transport interfaces.
"""

skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)

# The card declares ALL available transports in supported_interfaces; the
# list order is the server's preference (first entry = preferred transport).
# add_a2a_card() reads these and creates the appropriate transports.
name = "default/default/Weather_Agent_1.0.0"

agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supported_interfaces=[
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_PATTERNS,
            url=f"slim://localhost:46357/{name}",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.NATS_PATTERNS,
            url=f"nats://localhost:4222/{name}",
        ),
    ],
)

"""
Implement the agent logic and executor (same as Example 1).
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
        await event_queue.enqueue_event(new_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

"""
Serve via add_a2a_card() — one call does it all.
"""

async def main():
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=agent_card,
    )

    session = factory.create_app_session(max_sessions=10)
    await (
        session.add_a2a_card(agent_card, request_handler)
        .with_factory(factory)
        .start(keep_alive=True)
    )

if __name__ == "__main__":
    import asyncio
    import os

    # SLIM_SHARED_SECRET is required for SLIM transports
    if "SLIM_SHARED_SECRET" not in os.environ:
        os.environ["SLIM_SHARED_SECRET"] = "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"

    asyncio.run(main())
```

### Client: `weather_client_card.py`

The client is unchanged — `add_a2a_card()` is server-side only. Clients still use `factory.a2a(config).create(card)` with an agent card that has matching `supported_interfaces` for topic derivation.

```python
from a2a.helpers import get_stream_response_text, new_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Role,
    SendMessageRequest,
)

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import A2AExperimentalServer

# Reconstruct the same agent card as the server (for topic derivation)
agent_card = AgentCard(
    name="Weather Agent",
    version="1.0.0",
    default_input_modes=["text"],
    default_output_modes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[AgentSkill(id="weather_report", ...)],
    supported_interfaces=[
        AgentInterface(
            protocol_binding=InterfaceTransport.SLIM_PATTERNS,
            url="slim://localhost:46357/default/default/Weather_Agent_1.0.0",
        ),
        AgentInterface(
            protocol_binding=InterfaceTransport.NATS_PATTERNS,
            url="nats://localhost:4222/default/default/Weather_Agent_1.0.0",
        ),
    ],
    description="An agent that provides weather reports",
)

async def main():
    factory = AgntcyFactory()

    transport_type = "SLIM"  # or "NATS"
    transport = factory.create_transport(
        transport_type,
        endpoint="http://localhost:46357",
        name="default/default/weather_client_card",
    )

    # Returns a copy of the card whose preferred (first) interface is the
    # patterns transport, with the URL the server declares.
    card = A2AExperimentalServer.create_client_card(agent_card, transport_type)

    config = ClientConfig(slim_transport=transport)
    client = await factory.a2a(config).create(card)

    request = SendMessageRequest(
        message=new_text_message(
            "Hello, Weather Agent, how is the weather?", role=Role.ROLE_USER
        )
    )

    async for event in client.send_message(request):
        text = get_stream_response_text(event)
        if text:
            print(text)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

### Notes

- **Card as single source of truth:** The `AgentCard.supported_interfaces` list declares every transport the agent supports, in server-preference order (a2a-sdk 1.x dropped `preferred_transport`, `additional_interfaces` and `url` in favour of this single list). `add_a2a_card()` iterates over these interfaces, parses the URLs, creates transports, and registers containers — all automatically.
- **Interceptors:** Client interceptors use a2a-sdk 1.x's `ClientCallInterceptor` (`before(BeforeArgs)` / `after(AfterArgs)`); pass them to `factory.a2a(config).create(card, interceptors=[...])`. The `consumers` argument is deprecated and ignored.
- **`InterfaceTransport` constants:** Use `InterfaceTransport.SLIM_PATTERNS`, `.NATS_PATTERNS`, `.JSONRPC`, `.SLIM_RPC` instead of raw strings. Aliases like `InterfaceTransport.SLIM` (→ `"slimpatterns"`) and `.NATS` (→ `"natspatterns"`) are also available.
- **Fluent API options:**
  - `.with_factory(factory)` — reuse an existing `AgntcyFactory` (auto-created if omitted)
  - `.skip(transport_type)` — exclude a specific transport (e.g., `.skip("jsonrpc")`)
  - `.override(transport_type, target)` — supply a pre-built transport or config for a specific interface
  - `.with_public_url(url)` — advertise a reachable URL for the `JSONRPC` interface. The card's `JSONRPC` URL (e.g. `http://0.0.0.0:9000`) is where the server binds, which clients usually cannot reach; the served card carries `url` instead
  - `.with_slim_tls()` — connect the `slim` and `slimrpc` interfaces over TLS: explicit endpoints are dialled as `https://host:port`, and an `http://` `SLIM_ENDPOINT` is refused instead of connecting in plaintext
  - `.dry_run()` — returns a `ServeCardPlan` describing what _would_ be started, without starting anything
- **`SLIM_SHARED_SECRET`:** Required environment variable for any SLIM-based transport. Set it before calling `start()`.
- **URL formats:** Each interface URL supports two styles:
  - **Topic-only:** `slim://my_topic` or `slim://org/ns/agent` — endpoint resolved from `SLIM_ENDPOINT` env var (default: `http://localhost:46357`). A path without a port is part of the SLIM name, not a host.
  - **Explicit endpoint:** `slim://host:46357/my_topic` or `slim://host:46357/org/ns/agent` — endpoint extracted from the URL. A port is what marks the dataplane address.
- **Relayed replies (SLIM / NATS patterns):** a patterns server also accepts another agent's JSON-RPC success response as its input, as group chat does when it passes a reply to the next participant, and turns it into a `SendMessage` request. A Message reply is relayed as it is. A Task reply is relayed through its status message, or else the last agent message in its history. A Task with only artifacts, which is what a2a-sdk's `TaskUpdater.complete()` produces without a `message`, is rejected with `InvalidRequest`; an agent whose replies are relayed should complete with a message, for example `await updater.complete(message=...)`. The upstream `taskId` moves to `metadata["relayed_from"]`.
- **SLIM TLS:** a SLIM connection uses TLS when its endpoint is `https://`. That is an `https://` interface URL, `SLIM_ENDPOINT=https://...` for a topic-only URL, or `.with_slim_tls()` for a `slim://host:port/...` URL. Otherwise it is plaintext. The dataplane must present a certificate this host trusts. `.override()` configs are used as given.

### Running

First start the required services — see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/services/docker/docker-compose.yaml):

```bash
docker-compose -f services/docker/docker-compose.yaml up
```

Run the weather agent server:

```bash
uv run python weather_agent_card.py
```

You should see:

```
...
[agntcy_app_sdk.app_sessions] [INFO] App started. Waiting for shutdown signal (Ctrl+C)...
```

In another terminal, run the weather client:

```bash
uv run python weather_client_card.py
```

You should see the weather report: `The weather is sunny with a high of 75F.`

---

## Identity TBAC Integration

Activate Agntcy Identity Service TBAC by configuring the `IDENTITY_AUTH_ENABLED` and `IDENTITY_SERVICE_API_KEY` environment variable with the Identity App Service API key.
For more details, refer to the [official documentation](https://identity-docs.outshift.com/docs/dev#a2a-integration-using-the-python-sdk).

**Important**: Ensure the `IDENTITY_SERVICE_API_KEY` values for the client and server are different to enforce proper TBAC functionality.

What the SDK does when identity auth is enabled:

| Server transport | Token required | Rejection |
|---|---|---|
| HTTP JSON-RPC | On every request except the agent card at `/.well-known/agent-card.json` | HTTP 401 for a missing or malformed `Authorization` header, HTTP 403 for a token that fails verification |
| SLIM / NATS patterns | On every request | A JSON-RPC error reply |
| SLIM-RPC (`slimrpc`) | On every call. The SDK client sends the token as call metadata | RPC error `UNAUTHENTICATED` for a missing or malformed `Authorization` entry, `PERMISSION_DENIED` for a token that fails verification |

Each token is verified with a call to the Identity Service:

- The SLIM / NATS patterns server and the SLIM-RPC server make that call in their own pool of worker threads, so other requests keep being served while it waits. If the Identity Service does not answer within 10 seconds, the request is rejected like an invalid token. If the Identity Service SDK cannot be set up (for example, a missing setting), the server does not start: it fails before it subscribes, so it never serves a request it cannot check. A rejected request gets status 401 (`UNAUTHENTICATED` on SLIM-RPC) when the `Authorization` header is missing or malformed, and 403 (`PERMISSION_DENIED`) when the token is rejected or cannot be checked; the reply says only "Authentication failed", and the details go to the server log.
- The HTTP JSON-RPC server uses the Identity Service SDK's own middleware, which makes the call on the event loop with no deadline: while it waits, nothing else on that event loop runs (other SDK servers and clients in the same process included), and an Identity Service that never answers stalls them indefinitely. The server logs a warning about this at startup when identity auth is enabled. The middleware sets up the Identity Service SDK on the first request, so a missing setting gives an error response to each request rather than a startup failure. Its 403 body is `{"error": "forbidden", "reason": "Authentication failed: <error>"}`, where `<error>` is the Identity Service SDK's exception text, which can name internal hosts; this comes from identity SDK 0.0.7 and is not changed by this SDK.

The server also advertises the scheme on the agent card it serves: `IdentityServiceAuthScheme` (HTTP bearer, JWT) becomes the card's only entry in `security_requirements`, replacing any requirement you declared (a warning names the dropped ones, their schemes are removed from `security_schemes`, and skill requirements that name a removed scheme are dropped too, with a warning per skill), because the server enforces only this scheme. Schemes that no card-level requirement named, and skill requirements that name only such schemes, stay on the card and are not enforced, so remove them from the card yourself if the agent does not check them. `session.add_a2a_card(...)` serves a copy of your card, shared by every handler it builds, so your card object is not changed. A handler you build yourself, without `add_a2a_card`, edits the card you give it.

On the client side, SDK-built clients attach an Identity Service bearer token to every SLIM / NATS patterns request (including broadcast and group chat), to every SLIM-RPC call, and to every HTTP JSON-RPC request to a server whose agent card requires `IdentityServiceAuthScheme`, unless the request already has an `Authorization` header in any letter case (for example, from your own interceptor). Broadcast and group chat add the token to a copy, so the message you pass in is not changed. `slima2a`'s own `SRPCTransport` sends no call metadata, so the SDK's client uses a subclass that sends the call's service parameters, the token among them, as metadata, and keeps `slima2a`'s client tracing spans; a client built directly from `slima2a` sends no token and is rejected. A SLIM-RPC client has no served card to read, and is usually built from `minimal_agent_card(...)`, which declares no scheme, so it sends the token to every SLIM-RPC peer it calls: any peer that receives it can replay it to other agents that accept the same token until it expires, so only call SLIM-RPC peers you trust with it. An HTTP client built from a card that does not require the scheme sends no token; `A2AClientFactory.connect(url)` reads the served card, which does. With identity auth enabled, `A2AClientFactory.create()` returns a wrapper `Client` for HTTP JSON-RPC (when the card requires the scheme) and for SLIM-RPC, not a2a-sdk's `BaseClient`, so do not rely on `BaseClient`-only attributes. The token is fetched in a worker thread, so a slow Identity Service does not hold up other work, and it is reused until 30 seconds before the expiry (`exp`) it carries, for at most 5 minutes. A token without an expiry is fetched again for every request. Requests that need a token at the same moment share one fetch, and all get its token or its error. A fetch that takes more than 10 seconds is given up, and the request goes out without a token. When a SLIM / NATS patterns server rejects a request, the client returns an "Access Forbidden" message (a streaming call yields it and ends) and drops the cached token, so the next request fetches a new one; the request is not retried. When an HTTP JSON-RPC server answers 401 / 403, or a SLIM-RPC server answers `UNAUTHENTICATED` / `PERMISSION_DENIED`, the client drops the cached token and raises the error unchanged; the request is not retried either.

The bearer token is only as safe as the connection that carries it: over plain `http://` anyone on the network can read and replay it. Plain `http://` is allowed by default, because the SDK's demo apps run their agents that way. Set `IDENTITY_AUTH_ALLOW_HTTP=false` where agents should only be reached over `https://` (or behind a TLS-terminating proxy): `A2AClientFactory.create()` then raises `ValueError`, before sending anything, when it would send the token to an HTTP JSON-RPC agent at a plain `http://` URL on a host other than `localhost`, `127.0.0.1` or `::1`.

| Variable | Default | Effect |
|---|---|---|
| `IDENTITY_AUTH_ALLOW_HTTP` | `true` | `false` (or `0`, `no`, `off`, `disabled`) refuses to send the identity token to an HTTP JSON-RPC agent at a non-local plain `http://` URL. It does not cover SLIM, NATS, SLIM-RPC or MCP connections; secure those with TLS on the SLIM / NATS endpoint. With the flag false, a card advertising `http://0.0.0.0:…` makes `create()` raise: advertise a reachable URL with `with_public_url`. An unrecognised value logs a warning and counts as `true`. |

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
