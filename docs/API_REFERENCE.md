# AGNTCY Factory SDK - API Reference

## Overview

The AGNTCY Factory SDK provides a flexible framework for creating and managing agent communication transports and protocols. It supports multiple transport layers (SLIM, NATS, StreamableHTTP) and semantic protocols (A2A, MCP, FastMCP) with built-in observability and logging capabilities.

---

## Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Core Classes](#core-classes)
  - [AgntcyFactory](#agntcyfactory)
  - [AppSession](#appsession)
  - [AppContainer](#appcontainer)
- [Enumerations](#enumerations)
  - [ObservabilityProviders](#observabilityproviders)
  - [IdentityProviders](#identityproviders)
- [Methods Reference](#methods-reference)
  - [AgntcyFactory Methods](#agntcyfactory-methods-reference)
  - [AppSession Methods](#appsession-methods-reference)
  - [AppContainer Methods](#appcontainer-methods-reference)
- [Usage Examples](#usage-examples)
- [Best Practices](#best-practices)
- [Environment Variables](#environment-variables)

---

## Installation

```bash
pip install agntcy-app-sdk
```

### Dependencies

- `agntcy_app_sdk.transport.base`
- `agntcy_app_sdk.semantic.base`
- `agntcy_app_sdk.app_sessions`
- `ioa_observe.sdk` (optional, for tracing)

---

## Quick Start

```python
from agntcy_app_sdk.factory import AgntcyFactory

# Initialize the factory
factory = AgntcyFactory(
    name="MyAgentFactory",
    enable_tracing=True,
    log_level="INFO"
)

# Create an A2A client factory
a2a = factory.a2a()

# Create a transport
transport = factory.create_transport(
    transport="NATS",
    endpoint="nats://localhost:4222"
)
```

---

## Core Classes

### AgntcyFactory

The main factory class for creating agent gateway transports, protocols, and managing agent communication.

#### Constructor

```python
AgntcyFactory(
    name: str = "AgntcyFactory",
    enable_tracing: bool = False,
    log_level: str = "INFO"
)
```

**Parameters:**

| Parameter        | Type   | Default           | Description                                           |
| ---------------- | ------ | ----------------- | ----------------------------------------------------- |
| `name`           | `str`  | `"AgntcyFactory"` | Name identifier for the factory instance              |
| `enable_tracing` | `bool` | `False`           | Enable distributed tracing via ioa_observe            |
| `log_level`      | `str`  | `"INFO"`          | Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL) |

**Attributes:**

- `name` (str): Factory instance name
- `enable_tracing` (bool): Whether tracing is enabled
- `log_level` (str): Current logging level

**Raises:**

- `ValueError`: If an invalid log level is provided (defaults to INFO)

**Example:**

```python
# Basic initialization
factory = AgntcyFactory()

# With custom configuration
factory = AgntcyFactory(
    name="ProductionFactory",
    enable_tracing=True,
    log_level="DEBUG"
)
```

---

### AppSession

Manages the lifecycle of multiple agent application containers, providing centralized control over transport connections, protocol handlers, and agent directories. Each session can host multiple app containers, each representing a distinct agent service with its own transport layer and protocol implementation.

**Key Features:**

- Concurrent management of multiple agent containers
- Session lifecycle management (start, stop, individual or batch operations)
- Resource pooling with configurable session limits
- Async/await support for non-blocking operations
- Card-driven multi-transport bootstrap via `add_a2a_card()`

**Note:** Created via `AgntcyFactory.create_app_session()`

#### Constructor

```python
AppSession(max_sessions: int = 10)
```

**Parameters:**

| Parameter      | Type  | Default | Description                                         |
| -------------- | ----- | ------- | --------------------------------------------------- |
| `max_sessions` | `int` | `10`    | Maximum number of concurrent app containers allowed |

**Attributes:**

- `max_sessions` (int): Maximum allowed sessions
- `app_containers` (dict): Dictionary of session_id -> AppContainer mappings

**Example:**

```python
session = factory.create_app_session(max_sessions=20)
```

---

### AppContainer

Encapsulates all components required to run an agent application, including the server instance, transport layer, protocol handler, and optional directory service. An AppContainer manages the complete lifecycle of a single agent service.

**Note:** `AppContainer` instances are typically created automatically by `add_a2a_card()` (card-driven bootstrap) or `ContainerBuilder.build()` (manual builder). Direct instantiation is rarely needed.

**Key Features:**

- Automatic protocol handler detection and binding
- Lifecycle management (start, stop, graceful shutdown)
- Signal handling for SIGTERM and SIGINT
- Async/await support for non-blocking operations
- Automatic topic generation for A2A protocol
- Optional directory registration on startup

#### Constructor

```python
AppContainer(
    server: Any,
    transport: BaseTransport = None,
    directory: BaseAgentDirectory = None,
    topic: str = None,
    host: str = None,
    port: int = None
)
```

**Parameters:**

| Parameter   | Type                 | Required | Description                                                      |
| ----------- | -------------------- | -------- | ---------------------------------------------------------------- |
| `server`    | `Any`                | Yes      | Server instance (A2AStarletteApplication, MCPServer, or FastMCP) |
| `transport` | `BaseTransport`      | No       | Transport layer for agent communication                          |
| `directory` | `BaseAgentDirectory` | No       | Agent directory service for registration                         |
| `topic`     | `str`                | No       | Message topic/channel (auto-generated for A2A)                   |
| `host`      | `str`                | No       | Host address for the server                                      |
| `port`      | `int`                | No       | Port number for the server                                       |

**Attributes:**

- `server` (Any): The agent server instance
- `transport` (BaseTransport): Transport layer instance
- `directory` (BaseAgentDirectory): Directory service instance
- `topic` (str): Message topic/channel identifier
- `host` (str): Server host address
- `port` (int): Server port number
- `protocol_handler` (BaseAgentProtocol): Protocol handler instance
- `is_running` (bool): Current running state

**Supported Server Types:**

- `A2AStarletteApplication`: Agent-to-Agent protocol server
- `MCPServer`: Model Context Protocol server
- `FastMCP`: Fast Model Context Protocol server

**Example:**

```python
from agntcy_app_sdk.app_sessions import AppContainer

container = AppContainer(
    server=my_a2a_server,
    transport=nats_transport,
    topic="agents.my_agent"
)
```

---

## Enumerations

### ObservabilityProviders

Defines available observability providers.

```python
class ObservabilityProviders(Enum):
    IOA_OBSERVE = "ioa_observe"
```

---

### IdentityProviders

Defines available identity providers.

```python
class IdentityProviders(Enum):
    AGNTCY = "agntcy_identity"
```

---

## AppSession Methods Reference

### add_app_container()

Adds a new app container to the session manager.

```python
def add_app_container(
    session_id: str,
    container: AppContainer
) -> None
```

**Parameters:**

| Parameter    | Type           | Required | Description                       |
| ------------ | -------------- | -------- | --------------------------------- |
| `session_id` | `str`          | Yes      | Unique identifier for the session |
| `container`  | `AppContainer` | Yes      | AppContainer instance to add      |

**Raises:**

- `RuntimeError`: If maximum number of sessions has been reached

**Example:**

```python
session = factory.create_app_session(max_sessions=5)

container = AppContainer(
    server=my_server,
    transport=my_transport,
    topic="agents.processor"
)

session.add_app_container("processor-1", container)
```

---

### get_app_container()

Retrieves an app container by its session ID.

```python
def get_app_container(session_id: str) -> AppContainer | None
```

**Parameters:**

| Parameter    | Type  | Required | Description                       |
| ------------ | ----- | -------- | --------------------------------- |
| `session_id` | `str` | Yes      | Unique identifier for the session |

**Returns:** AppContainer instance or None if not found

**Example:**

```python
container = session.get_app_container("processor-1")
if container:
    print(f"Container running: {container.is_running}")
```

---

### remove_app_container()

Removes an app container from the session manager.

```python
def remove_app_container(session_id: str) -> None
```

**Parameters:**

| Parameter    | Type  | Required | Description                       |
| ------------ | ----- | -------- | --------------------------------- |
| `session_id` | `str` | Yes      | Unique identifier for the session |

**Raises:**

- `RuntimeError`: If attempting to remove a running session (must be stopped first)

**Example:**

```python
# Stop the session first
await session.stop_session("processor-1")

# Then remove it
session.remove_app_container("processor-1")
```

---

### start_session()

Starts a specific app container by its session ID.

```python
async def start_session(
    session_id: str,
    keep_alive: bool = False,
    push_to_directory_on_startup: bool = False,
    **kwargs
) -> None
```

**Parameters:**

| Parameter                      | Type   | Default  | Description                                             |
| ------------------------------ | ------ | -------- | ------------------------------------------------------- |
| `session_id`                   | `str`  | Required | Unique identifier for the session                       |
| `keep_alive`                   | `bool` | `False`  | Keep session running indefinitely until shutdown signal |
| `push_to_directory_on_startup` | `bool` | `False`  | Register agent in directory service on startup          |
| `**kwargs`                     | `dict` | `{}`     | Additional arguments (reserved for future use)          |

**Raises:**

- `ValueError`: If no app container found for the given session_id

**Example:**

```python
# Start with basic configuration
await session.start_session("processor-1")

# Start with keep_alive and directory registration
await session.start_session(
    "processor-1",
    keep_alive=True,
    push_to_directory_on_startup=True
)
```

---

### stop_session()

Stops a specific app container by its session ID.

```python
async def stop_session(session_id: str) -> None
```

**Parameters:**

| Parameter    | Type  | Required | Description                       |
| ------------ | ----- | -------- | --------------------------------- |
| `session_id` | `str` | Yes      | Unique identifier for the session |

**Raises:**

- `ValueError`: If no app container found for the given session_id

**Example:**

```python
await session.stop_session("processor-1")
```

---

### start_all_sessions()

Starts all app containers in the session manager.

```python
async def start_all_sessions(
    keep_alive: bool = False,
    push_to_directory_on_startup: bool = False
) -> None
```

**Parameters:**

| Parameter                      | Type   | Default | Description                                     |
| ------------------------------ | ------ | ------- | ----------------------------------------------- |
| `keep_alive`                   | `bool` | `False` | Keep all sessions running until shutdown signal |
| `push_to_directory_on_startup` | `bool` | `False` | Register all agents in directory on startup     |

**Example:**

```python
# Start all sessions with directory registration
await session.start_all_sessions(
    keep_alive=True,
    push_to_directory_on_startup=True
)
```

---

### stop_all_sessions()

Stops all running app containers in the session manager.

```python
async def stop_all_sessions() -> None
```

**Example:**

```python
# Gracefully stop all running sessions
await session.stop_all_sessions()
```

---

### add_a2a_card()

Begin building containers from an A2A AgentCard's `additional_interfaces`. Returns a `CardBuilder` for fluent configuration.

```python
def add_a2a_card(
    agent_card: AgentCard,
    request_handler: DefaultRequestHandler
) -> CardBuilder
```

**Parameters:**

| Parameter         | Type                    | Required | Description                                      |
| ----------------- | ----------------------- | -------- | ------------------------------------------------ |
| `agent_card`      | `AgentCard`             | Yes      | Agent card with `additional_interfaces` declared |
| `request_handler` | `DefaultRequestHandler` | Yes      | Request handler for A2A message processing       |

**Returns:** `CardBuilder` instance for fluent configuration

**Example:**

```python
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport
from a2a.types import AgentCard, AgentInterface

agent_card = AgentCard(
    ...,
    additional_interfaces=[
        AgentInterface(transport=InterfaceTransport.SLIM_PATTERNS, url="slim://host:46357/topic"),
        AgentInterface(transport=InterfaceTransport.NATS_PATTERNS, url="nats://host:4222/topic"),
    ],
)

session = factory.create_app_session(max_sessions=10)
await (
    session.add_a2a_card(agent_card, request_handler)
    .with_factory(factory)
    .start(keep_alive=True)
)
```

---

## AppContainer Methods Reference

### set_transport()

Sets or updates the transport layer for the container.

```python
def set_transport(transport: BaseTransport) -> None
```

**Parameters:**

| Parameter   | Type            | Required | Description               |
| ----------- | --------------- | -------- | ------------------------- |
| `transport` | `BaseTransport` | Yes      | Transport instance to use |

**Example:**

```python
nats_transport = factory.create_transport("NATS", endpoint="nats://localhost:4222")
container.set_transport(nats_transport)
```

---

### set_directory()

Sets or updates the agent directory service for the container.

```python
def set_directory(directory: BaseAgentDirectory) -> None
```

**Parameters:**

| Parameter   | Type                 | Required | Description                |
| ----------- | -------------------- | -------- | -------------------------- |
| `directory` | `BaseAgentDirectory` | Yes      | Directory service instance |

**Example:**

```python
from agntcy_app_sdk.directory import AgentDirectory

directory = AgentDirectory(endpoint="https://directory.example.com")
container.set_directory(directory)
```

---

### set_topic()

Sets or updates the message topic/channel for the container.

```python
def set_topic(topic: str) -> None
```

**Parameters:**

| Parameter | Type  | Required | Description              |
| --------- | ----- | -------- | ------------------------ |
| `topic`   | `str` | Yes      | Topic/channel identifier |

**Example:**

```python
container.set_topic("agents.analytics.processor")
```

---

### run()

Starts all components of the app container and begins processing messages.

```python
async def run(
    keep_alive: bool = False,
    push_to_directory_on_startup: bool = False
) -> None
```

**Parameters:**

| Parameter                      | Type   | Default | Description                                           |
| ------------------------------ | ------ | ------- | ----------------------------------------------------- |
| `keep_alive`                   | `bool` | `False` | Keep container running until shutdown signal received |
| `push_to_directory_on_startup` | `bool` | `False` | Register agent in directory service on startup        |

**Raises:**

- `ValueError`: If transport, protocol_handler, or topic is not set

**Startup Sequence:**

1. Initializes transport layer (calls `transport.setup()`)
2. Initializes directory service if configured (calls `directory.setup()`)
3. Sets message callback handler
4. Subscribes to the specified topic
5. Optionally registers agent in directory
6. Initializes protocol handler (calls `protocol_handler.setup()`)
7. Enters keep-alive loop if `keep_alive=True`

**Example:**

```python
# Run without keep_alive (returns immediately after setup)
await container.run()

# Run with keep_alive (blocks until shutdown signal)
await container.run(
    keep_alive=True,
    push_to_directory_on_startup=True
)
```

---

### stop()

Stops all components of the app container gracefully.

```python
async def stop() -> None
```

**Shutdown Sequence:**

1. Closes transport connection
2. Sets `is_running` to False
3. Logs shutdown completion

**Example:**

```python
await container.stop()
```

---

### loop_forever()

Keeps the event loop running indefinitely until a shutdown signal is received. Handles SIGTERM and SIGINT signals gracefully.

```python
async def loop_forever() -> None
```

**Behavior:**

- Registers signal handlers for SIGTERM and SIGINT
- Blocks until shutdown signal received
- Automatically calls `stop()` on shutdown
- Handles asyncio.CancelledError gracefully

**Note:** This method is automatically called when `run(keep_alive=True)` is used.

**Example:**

```python
# Typically not called directly, but can be used standalone
await container.run(keep_alive=False)  # Setup only
await container.loop_forever()  # Then keep alive
```

---

## AgntcyFactory Methods Reference

### CardBuilder

Fluent builder that expands an `AgentCard`'s `additional_interfaces` into containers. Constructed via `AppSession.add_a2a_card()`.

The builder reads `agent_card.additional_interfaces`, creates transport/config objects for each, and registers `AppContainer` instances on the session.

#### Methods

##### with_factory()

Use an existing `AgntcyFactory` instead of auto-creating one.

```python
def with_factory(factory: AgntcyFactory) -> CardBuilder
```

**Parameters:**

| Parameter | Type            | Required | Description               |
| --------- | --------------- | -------- | ------------------------- |
| `factory` | `AgntcyFactory` | Yes      | Factory instance to reuse |

**Returns:** `CardBuilder` (for chaining)

---

##### skip()

Exclude an interface type from being started.

```python
def skip(transport_type: str) -> CardBuilder
```

**Parameters:**

| Parameter        | Type  | Required | Description                                        |
| ---------------- | ----- | -------- | -------------------------------------------------- |
| `transport_type` | `str` | Yes      | Transport to skip (e.g., `"jsonrpc"`, `"slimrpc"`) |

**Returns:** `CardBuilder` (for chaining)

---

##### override()

Provide a pre-built config or transport for a specific interface type.

```python
def override(transport_type: str, target: object) -> CardBuilder
```

**Parameters:**

| Parameter        | Type     | Required | Description                                          |
| ---------------- | -------- | -------- | ---------------------------------------------------- |
| `transport_type` | `str`    | Yes      | Transport to override                                |
| `target`         | `object` | Yes      | Pre-built transport or config for the interface type |

**Override targets by transport:**

- `slimrpc`: pass a pre-built `A2ASlimRpcServerConfig`
- `slimpatterns` / `natspatterns`: pass a pre-built `BaseTransport`
- `jsonrpc` / `http`: pass a pre-built `A2AStarletteApplication`

**Returns:** `CardBuilder` (for chaining)

---

##### dry_run()

Return a plan describing what would be started, without actually starting anything.

```python
async def dry_run() -> ServeCardPlan
```

**Returns:** `ServeCardPlan` instance

**Example:**

```python
plan = await (
    session.add_a2a_card(agent_card, handler)
    .with_factory(factory)
    .dry_run()
)
print(plan)
# serve_card plan:
#   [slim-0] slimpatterns -> endpoint=http://localhost:46357, topic=my_topic, name=my_topic
#   [nats-1] natspatterns -> endpoint=nats://localhost:4222, topic=my_topic
```

---

##### start()

Build all containers and start sessions.

```python
async def start(*, keep_alive: bool = False) -> None
```

**Parameters:**

| Parameter    | Type   | Default | Description                                     |
| ------------ | ------ | ------- | ----------------------------------------------- |
| `keep_alive` | `bool` | `False` | Keep all sessions running until shutdown signal |

---

### InterfaceTransport

Valid transport identifiers for `AgentInterface.transport`. Use these constants instead of hard-coded strings when building `AgentCard.additional_interfaces`.

```python
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport
```

**Canonical Types:**

| Constant                           | Value            | Description                             |
| ---------------------------------- | ---------------- | --------------------------------------- |
| `InterfaceTransport.SLIM_RPC`      | `"slimrpc"`      | SLIM-RPC (protobuf-over-SLIM) transport |
| `InterfaceTransport.SLIM_PATTERNS` | `"slimpatterns"` | SLIM pub/sub patterns transport         |
| `InterfaceTransport.NATS_PATTERNS` | `"natspatterns"` | NATS pub/sub patterns transport         |
| `InterfaceTransport.JSONRPC`       | `"jsonrpc"`      | HTTP JSON-RPC transport (standard A2A)  |
| `InterfaceTransport.HTTP`          | `"http"`         | Alias for `jsonrpc`                     |

**Convenience Aliases:**

| Alias                              | Resolves To      |
| ---------------------------------- | ---------------- |
| `InterfaceTransport.SLIM`          | `"slimpatterns"` |
| `InterfaceTransport.NATS`          | `"natspatterns"` |
| `InterfaceTransport.SLIM_EXTENDED` | `"slimpatterns"` |

**Class Methods:**

- `InterfaceTransport.all_types()` — Returns the full set of accepted strings (canonical + aliases)
- `InterfaceTransport.canonical_types()` — Returns only canonical (non-alias) transport types

---

### ServeCardPlan

Describes what `CardBuilder.start()` _would_ start (dry-run output).

```python
@dataclass
class ServeCardPlan:
    containers: list[dict[str, str]]
```

Each entry in `containers` is a dict with the following keys:

| Key          | Type  | Description                                           |
| ------------ | ----- | ----------------------------------------------------- |
| `session_id` | `str` | The session ID that would be assigned                 |
| `transport`  | `str` | The canonical transport type (e.g., `"slimpatterns"`) |
| `detail`     | `str` | Human-readable config summary (endpoint, topic, etc.) |

**Example output:**

```
serve_card plan:
  [slim-0] slimpatterns -> endpoint=http://localhost:46357, topic=default/default/My_Agent_1.0.0, name=default/default/My_Agent_1.0.0
  [nats-1] natspatterns -> endpoint=nats://localhost:4222, topic=default/default/My_Agent_1.0.0
  [http-2] jsonrpc -> host=0.0.0.0, port=9000
```

---

### registered_protocols()

Returns a list of all registered protocol types.

```python
def registered_protocols() -> list[str]
```

**Returns:** List of registered protocol type names

**Example:**

```python
protocols = factory.registered_protocols()
print(protocols)  # ['A2A', 'MCP', 'FastMCP']
```

---

### registered_transports()

Returns a list of all registered transport types.

```python
def registered_transports() -> list[str]
```

**Returns:** List of registered transport type names

**Example:**

```python
transports = factory.registered_transports()
print(transports)  # ['SLIM', 'NATS', 'STREAMABLE_HTTP']
```

---

### registered_observability_providers()

Returns a list of all registered observability providers.

```python
def registered_observability_providers() -> list[str]
```

**Returns:** List of observability provider names

**Example:**

```python
providers = factory.registered_observability_providers()
print(providers)  # ['ioa_observe']
```

---

### a2a()

Returns a typed A2A client factory.

```python
def a2a(
    config: ClientConfig | None = None
) -> A2AClientFactory
```

**Parameters:**

| Parameter | Type                   | Required | Description              |
| --------- | ---------------------- | -------- | ------------------------ |
| `config`  | `ClientConfig \| None` | No       | A2A client configuration |

**Returns:** `A2AClientFactory` instance

**Example:**

```python
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

# Create A2A client factory with default config
a2a = factory.a2a()

# Create with SLIM transport config
a2a = factory.a2a(ClientConfig(slim_transport=my_transport))

# Then create a client from a card
client = await a2a.create(agent_card)

# Or use the convenience connect() method
client = await a2a.connect("https://agent.example.com")
```

---

### mcp()

Returns a typed MCP client factory.

```python
def mcp() -> MCPClientFactory
```

**Returns:** `MCPClientFactory` instance

**Example:**

```python
# Create MCP client
mcp_client = await factory.mcp().create_client(
    topic="agents.processing",
    transport=my_transport
)
```

---

### fast_mcp()

Returns a typed FastMCP client factory.

```python
def fast_mcp() -> FastMCPClientFactory
```

**Returns:** `FastMCPClientFactory` instance

**Example:**

```python
# Create FastMCP client
client = await factory.fast_mcp().create_client(
    url="http://localhost:8081/mcp",
    topic="my_agent",
    transport=my_transport
)
```

---

### create_app_session()

Creates an app session to manage multiple app containers.

```python
def create_app_session(
    max_sessions: int = 10
) -> AppSession
```

**Parameters:**

| Parameter      | Type  | Default | Description                           |
| -------------- | ----- | ------- | ------------------------------------- |
| `max_sessions` | `int` | `10`    | Maximum number of concurrent sessions |

**Returns:** AppSession instance

**Example:**

```python
session = factory.create_app_session(max_sessions=20)
```

---

### create_transport()

Creates a transport instance for the specified transport type.

```python
def create_transport(
    transport: str,
    name: str | None = None,
    client: Any | None = None,
    endpoint: str | None = None,
    **kwargs
) -> BaseTransport
```

**Parameters:**

| Parameter   | Type          | Required | Description                              |
| ----------- | ------------- | -------- | ---------------------------------------- |
| `transport` | `str`         | Yes      | Transport type (e.g., "NATS", "SLIM")    |
| `name`      | `str \| None` | No       | Custom name for the transport instance   |
| `client`    | `Any \| None` | No\*     | Existing client instance                 |
| `endpoint`  | `str \| None` | No\*     | Connection endpoint URL                  |
| `**kwargs`  | `dict`        | No       | Additional transport-specific parameters |

**\*Note:** Either `client` or `endpoint` must be provided.

**Returns:** Transport instance for the specified type

**Raises:**

- `ValueError`: If neither `client` nor `endpoint` is provided
- Returns `None` if transport type is not registered (with warning log)

**Example:**

```python
# Create transport from endpoint
transport = factory.create_transport(
    transport="NATS",
    name="MainNatsTransport",
    endpoint="nats://localhost:4222"
)

# Create transport from existing client
import nats

nats_client = nats.connect("nats://localhost:4222")
transport = factory.create_transport(
    transport="NATS",
    client=nats_client
)
```

---

### register_transport() (Class Method)

Decorator to register a custom transport implementation.

```python
@classmethod
def register_transport(cls, transport_type: str)
```

**Parameters:**

| Parameter        | Type  | Description                 |
| ---------------- | ----- | --------------------------- |
| `transport_type` | `str` | Name for the transport type |

**Returns:** Decorator function

**Example:**

```python
from agntcy_app_sdk.transport.base import BaseTransport

@AgntcyFactory.register_transport("CUSTOM")
class CustomTransport(BaseTransport):
    # Implementation here
    pass
```

---

## Usage Examples

### Card-Driven Multi-Transport Server

```python
import asyncio
import os
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.semantic.a2a.server.card_bootstrap import InterfaceTransport

os.environ["SLIM_SHARED_SECRET"] = "my-secret-at-least-32-chars-long-xxxxx"

agent_card = AgentCard(
    name="My Agent",
    description="Multi-transport agent",
    url="http://localhost:9000",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[AgentSkill(id="hello", name="Hello", description="Says hello",
                       tags=["hello"], examples=["hi"])],
    preferredTransport=InterfaceTransport.SLIM_PATTERNS,
    additional_interfaces=[
        AgentInterface(transport=InterfaceTransport.SLIM_PATTERNS,
                       url="slim://localhost:46357/default/default/My_Agent_1.0.0"),
        AgentInterface(transport=InterfaceTransport.NATS_PATTERNS,
                       url="nats://localhost:4222/default/default/My_Agent_1.0.0"),
        AgentInterface(transport=InterfaceTransport.JSONRPC,
                       url="http://0.0.0.0:9000"),
    ],
    supportsAuthenticatedExtendedCard=False,
)

async def main():
    factory = AgntcyFactory()
    handler = DefaultRequestHandler(
        agent_executor=MyAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    session = factory.create_app_session(max_sessions=10)
    await (
        session.add_a2a_card(agent_card, handler)
        .with_factory(factory)
        .start(keep_alive=True)
    )

asyncio.run(main())
```

---

### Basic Client Creation

```python
from agntcy_app_sdk.factory import AgntcyFactory

# Initialize factory
factory = AgntcyFactory(log_level="DEBUG")

# Create an A2A client via card resolution
client = await factory.a2a().connect("https://api.agent.example.com")

# Use the client
response = client.send_message("Hello, agent!")
```

---

### Using Multiple Transports

```python
# Create SLIM transport
slim_transport = factory.create_transport(
    transport="SLIM",
    endpoint="http://localhost:46357",
    name="org/namespace/my_agent_topic"
)

# Create NATS transport
nats_transport = factory.create_transport(
    transport="NATS",
    endpoint="nats://localhost:4222"
)

# Create clients with different transports
from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

a2a = factory.a2a(ClientConfig(slim_transport=slim_transport))
a2a_client = await a2a.connect(A2A_CARD)
```

---

### Enabling Observability

```python
import os

# Set up observability endpoint
os.environ["OTLP_HTTP_ENDPOINT"] = "http://observability.example.com:4318"

# Create factory with tracing enabled
factory = AgntcyFactory(
    name="TracedFactory",
    enable_tracing=True,
    log_level="INFO"
)

# All operations will now be traced
client = await factory.a2a().connect(
    "https://agent.example.com"
)
```

---

### Managing App Sessions

```python
# Create a session manager
session = factory.create_app_session(max_sessions=15)

# Use the session to manage multiple app containers
# (Session management details depend on AppSession implementation)
```

---

### Complete App Container Lifecycle

```python
import asyncio
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_sessions import AppContainer

async def main():
    # Initialize factory
    factory = AgntcyFactory(log_level="INFO")

    # Create transport
    transport = factory.create_transport(
        transport="SLIM",
        endpoint="http://localhost:46357",
        name="org/namespace/my_agent_topic"
    )

    # Create app container
    container = AppContainer(
        server=my_a2a_server,  # Your A2A server instance
        transport=transport,
        topic="agents.my_agent"
    )

    # Run the container with keep_alive
    try:
        await container.run(
            keep_alive=True,
            push_to_directory_on_startup=True
        )
    except KeyboardInterrupt:
        print("Shutting down...")
        await container.stop()

# Run the async main function
asyncio.run(main())
```

---

### Managing Multiple Agents with AppSession

```python
import asyncio
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_sessions import AppContainer

async def main():
    factory = AgntcyFactory()

    # Create session manager
    session = factory.create_app_session(max_sessions=10)

    # Create SLIM transport
    slim_transport = factory.create_transport(
        transport="SLIM",
        endpoint="http://localhost:46357",
        name="org/namespace/my_agent_topic"
    )

    # Create multiple app containers
    agents = [
        ("processor-1", "agents.processor.instance1", processor_server_1),
        ("processor-2", "agents.processor.instance2", processor_server_2),
        ("analyzer-1", "agents.analyzer.instance1", analyzer_server_1),
    ]

    # Add containers to session
    for session_id, topic, server in agents:
        container = AppContainer(
            server=server,
            transport=nats_transport,
            topic=topic
        )
        session.add_app_container(session_id, container)

    # Start all agents
    await session.start_all_sessions()

    print("All agents started successfully")

    # Keep running until interrupted
    try:
        await asyncio.Event().wait()
    except KeyboardInterrupt:
        print("Shutting down all agents...")
        await session.stop_all_sessions()

asyncio.run(main())
```

---

### Managing Individual Sessions

```python
async def manage_sessions():
    factory = AgntcyFactory()
    session = factory.create_app_session(max_sessions=5)

    # Add containers
    container1 = AppContainer(server=server1, transport=transport, topic="agents.agent1")
    container2 = AppContainer(server=server2, transport=transport, topic="agents.agent2")

    session.add_app_container("agent1", container1)
    session.add_app_container("agent2", container2)

    # Start individual sessions
    await session.start_session("agent1")
    print("Agent 1 started")

    # Do some work...
    await asyncio.sleep(10)

    # Start second agent
    await session.start_session("agent2")
    print("Agent 2 started")

    # Later, stop individual sessions
    await session.stop_session("agent1")
    print("Agent 1 stopped")

    # Remove stopped container
    session.remove_app_container("agent1")

    # Clean up remaining sessions
    await session.stop_all_sessions()
```

### Checking Available Components

```python
# List all available protocols
protocols = factory.registered_protocols()
print(f"Available protocols: {protocols}")

# List all available transports
transports = factory.registered_transports()
print(f"Available transports: {transports}")

# List observability providers
providers = factory.registered_observability_providers()
print(f"Observability providers: {providers}")
```

---

### Custom Transport Registration

```python
from agntcy_app_sdk.transport.base import BaseTransport

@AgntcyFactory.register_transport("REDIS")
class RedisTransport(BaseTransport):
    def __init__(self, endpoint, name=None, **kwargs):
        super().__init__(name=name)
        self.endpoint = endpoint
        # Initialize Redis connection

    @classmethod
    def from_client(cls, client, name=None, **kwargs):
        # Create from existing Redis client
        pass

    @classmethod
    def from_config(cls, endpoint, name=None, **kwargs):
        return cls(endpoint=endpoint, name=name, **kwargs)

# Now use the custom transport
factory = AgntcyFactory()
redis_transport = factory.create_transport(
    transport="REDIS",
    endpoint="redis://localhost:6379"
)
```

---

## Environment Variables

### Tracing Configuration

| Variable             | Description                      | Default                           |
| -------------------- | -------------------------------- | --------------------------------- |
| `TRACING_ENABLED`    | Enable/disable tracing           | Set by `enable_tracing` parameter |
| `OTLP_HTTP_ENDPOINT` | OpenTelemetry collector endpoint | `http://localhost:4318`           |

---

## Best Practices

### Factory Usage

1. **Initialize Once**: Create a single `AgntcyFactory` instance and reuse it throughout your application.

2. **Enable Tracing in Production**: Use `enable_tracing=True` with appropriate OTLP endpoints for production monitoring.

3. **Use Appropriate Log Levels**: Set `log_level="DEBUG"` during development and `log_level="WARNING"` or `log_level="ERROR"` in production.

4. **Handle Errors Gracefully**: Always wrap client creation in try-except blocks to handle configuration errors.

5. **Reuse Transports**: Create transport instances once and reuse them across multiple clients when possible.
