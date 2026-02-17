# MCP Usage Guide

In this guide, we will walk through the key features of the Agntcy Application SDK's MCP (Model Context Protocol) integration and explore end-to-end examples of creating MCP servers and clients that communicate over abstract transports (SLIM, NATS).

## Architecture

The SDK supports two MCP variants — **MCP** (low-level `mcp.server.lowlevel.Server`) and **FastMCP** (high-level `mcp.server.fastmcp.FastMCP`). Both use the same transport layer, but differ in their protocol bridge and client model.

```
                                AgntcyFactory
                    ┌───────────┬──────┴──────┬────────────────┐
                    v           v             v                v
                 .mcp()    .fast_mcp()   .create_transport()  .create_app_session()
                    │           │             │                       │
                    v           v             v                  AppSession
             MCPClientFactory  FastMCPClient  BaseTransport    .add(target)
                    │          Factory     (SLIM / NATS)     .with_transport()
                    v           │                            .with_topic()
              ClientSession     v                                    │
                            MCPClient                             .build()
                                                              ┌──────┴──────┐
                                                              v              v
       CLIENT SIDE                               MCPServerHandler   FastMCPServerHandler
      ─────────────                              (transport req'd)  (transport optional)
       SERVER SIDE                                      │                   │
                                                        v                   v
                                                  MCP server.run()    Uvicorn/ASGI
                                                 (memory streams)    (HTTP :8081)
                                                        │            + opt. transport
                                                        v                   │
                                                   SLIM / NATS              v
                                                                    HTTP + SLIM / NATS
```

**Handler auto-detection** — When you call `session.add(target).build()`, the SDK inspects the `target` type:

| Target type | Transport provided? | Handler selected                                                   |
| ----------- | ------------------- | ------------------------------------------------------------------ |
| `MCPServer` | Yes (required)      | `MCPServerHandler` — bridges MCP via memory streams over transport |
| `FastMCP`   | Optional            | `FastMCPServerHandler` — runs Uvicorn + optional transport bridge  |

**MCP vs FastMCP — key differences:**

| Aspect              | MCP (`MCPServerHandler`)                            | FastMCP (`FastMCPServerHandler`)              |
| ------------------- | --------------------------------------------------- | --------------------------------------------- |
| **Server type**     | `mcp.server.lowlevel.Server`                        | `mcp.server.fastmcp.FastMCP`                  |
| **Transport**       | Required — no HTTP fallback                         | Optional — always runs HTTP via Uvicorn       |
| **Protocol bridge** | `MCPProtocol` — bidirectional memory streams        | `FastMCPProtocol` — ASGI simulation           |
| **Client type**     | `ClientSession` (from `mcp` package, async ctx mgr) | `MCPClient` (SDK class, point-to-point)       |
| **Message flow**    | JSON-RPC → memory stream → `server.run()` → stream  | JSON-RPC → ASGI scope → `streamable_http_app` |

The following table summarizes current MCP transport support:

| Handler \ Transport | SLIM | NATS |    HTTP     |
| ------------------- | :--: | :--: | :---------: |
| **MCP**             |  ✅  |  ✅  |      —      |
| **FastMCP**         |  ✅  |  ✅  | ✅ (always) |

---

## Setup

We will use `uv` for package management and virtual environments. If you don't have it installed, you can install it via:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a new project directory:

```bash
uv init agntcy-mcp
cd agntcy-mcp
```

Install the Agntcy Application SDK:

```bash
uv add agntcy-app-sdk
```

---

## Example 1 — MCP over SLIM / NATS

The MCP path bridges the low-level `mcp.server.lowlevel.Server` over an abstract transport using bidirectional memory streams. The transport is required — there is no HTTP fallback.

### Server: `weather_server.py`

```python
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_sessions import AppContainer
from mcp.server.fastmcp import FastMCP
import asyncio

# Create an MCP server instance
mcp = FastMCP()

# Add a tool to the MCP server
@mcp.tool()
async def get_forecast(location: str) -> str:
    return "Temperature: 30°C\n" "Humidity: 50%\n" "Condition: Sunny\n"

# Initialize the Agntcy factory
factory = AgntcyFactory()

# Create a transport instance (swap "SLIM" for "NATS" to use NATS)
transport = factory.create_transport(
    "SLIM", endpoint="http://localhost:46357", name="default/default/weather_server"
)

async def main():
    # Create an app session and serve the MCP server via an AppContainer.
    # Note: we pass mcp._mcp_server (the low-level Server) since MCPServerHandler
    # requires it. The handler auto-detection picks MCPServerHandler for this type.
    app_session = factory.create_app_session(max_sessions=1)
    app_container = AppContainer(
        mcp._mcp_server, transport=transport, topic="my_weather_agent.mcp"
    )
    app_session.add_app_container("default_session", app_container)
    await app_session.start_all_sessions(keep_alive=True)

if __name__ == "__main__":
    asyncio.run(main())
```

### Client: `weather_client.py`

```python
from agntcy_app_sdk.factory import AgntcyFactory
import asyncio

factory = AgntcyFactory()
transport = factory.create_transport(
    "SLIM", endpoint="http://localhost:46357", name="default/default/weather_client"
)
# transport = factory.create_transport("NATS", endpoint="localhost:4222")

async def main():
    # Create an MCP client — returns an async context manager wrapping a ClientSession
    mcp_client = await factory.mcp().create_client(
        topic="default/default/weather_server",
        transport=transport,
    )
    async with mcp_client as client:
        tools = await client.list_tools()
        print("[test] Tools available:", tools)

        result = await client.call_tool(
            name="get_forecast",
            arguments={"location": "Colombia"},
        )
        print(f"Tool call result: {result}")

if __name__ == "__main__":
    asyncio.run(main())
```

A few notes:

- The server does not bind to a host and port — it listens on the given topic via the transport. Both client and server must use the same topic to communicate.
- Swapping `"SLIM"` for `"NATS"` (and changing the endpoint) is all that's needed to switch transports.

### Running

First start the SLIM transport server — see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/services/docker/docker-compose.yaml) or SLIM [repo](https://github.com/agntcy/slim/tree/main).

Run the weather server:

```bash
uv run python weather_server.py
```

You should see:

```
[agntcy_app_sdk.transport.slim.transport] [INFO] Subscribed to default/default/my_weather_agent.mcp
```

In another terminal, run the weather client:

```bash
uv run python weather_client.py
```

You should see:

```
Tool call result: meta=None content=[TextContent(type='text', text='Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n', annotations=None, meta=None)] structuredContent={'result': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'} isError=False
```

---

## Example 2 — Practical multi-server monitoring

The `examples/mcp/` directory demonstrates how **multiple MCP servers register on different topics** and how **one client can reach each server independently** by targeting its topic. The example uses [psutil](https://github.com/giampaolo/psutil) for host metrics and [docker-py](https://github.com/docker/docker-py) for container metrics.

The examples are packaged as their own `uv` project. Install all dependencies:

```bash
cd examples/mcp
uv sync
```

### Two servers, two topics

Each server is a standalone process that registers its tools on its own topic:

**Host monitor** (`host_monitor_server.py`, topic `host_monitor.mcp`):

- `get_cpu_usage()` — per-core and overall CPU utilization
- `get_memory_usage()` — RAM total, available, used, percent
- `get_system_summary()` — combined CPU + memory + uptime

**Docker monitor** (`docker_monitor_server.py`, topic `docker_monitor.mcp`):

- `list_containers()` — running containers with name, image, status
- `get_container_stats(container_name_or_id)` — CPU% and memory for one container
- `get_all_container_stats()` — CPU/memory overview for all running containers

Both servers follow the same pattern — create a `FastMCP`, define tools, wire to a transport via `AppContainer`:

```python
mcp = FastMCP()

@mcp.tool()
async def get_cpu_usage() -> str:
    """Return per-core and overall CPU utilization percentages."""
    ...

transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)
app_session = factory.create_app_session(max_sessions=1)
app_container = AppContainer(
    mcp._mcp_server, transport=transport, topic="host_monitor.mcp"
)
app_session.add_app_container("default_session", app_container)
await app_session.start_all_sessions(keep_alive=True)
```

### One client, multiple topics

The client (`monitoring_client.py`) accepts a `--topics` argument listing the topics to query (defaults to both `host_monitor.mcp` and `docker_monitor.mcp`). For each topic it creates a separate MCP client, lists the available tools, then calls a representative tool:

```python
async def _query_server(topic, transport_type, endpoint):
    transport = factory.create_transport(transport_type, endpoint=endpoint, ...)
    mcp_client = await factory.mcp().create_client(topic=topic, transport=transport)
    async with mcp_client as client:
        tools = await client.list_tools()
        tool_names = [t.name for t in tools.tools]

        if "get_system_summary" in tool_names:
            result = await client.call_tool(name="get_system_summary", arguments={})

        if "list_containers" in tool_names:
            result = await client.call_tool(name="list_containers", arguments={})

# Query both servers from one client process
for topic in topics:
    await _query_server(topic, transport_type, endpoint)
```

### Running the example

From the `examples/mcp` directory:

```bash
# Terminal 1 — start the host monitor server
uv run python host_monitor_server.py --transport SLIM --endpoint http://localhost:46357

# Terminal 2 — start the Docker monitor server (Docker daemon must be running)
uv run python docker_monitor_server.py --transport SLIM --endpoint http://localhost:46357

# Terminal 3 — run the client (queries both servers via topics)
uv run python monitoring_client.py --transport SLIM --endpoint http://localhost:46357
```

You can also query only one server by passing a single topic:

```bash
uv run python monitoring_client.py --transport SLIM --endpoint http://localhost:46357 --topics host_monitor.mcp
```

> **Note:** Use `--transport NATS --endpoint localhost:4222` for NATS instead of SLIM. See the full source in [`examples/mcp/`](../examples/mcp/).

---

## Example 3 — FastMCP (Streamable HTTP + Optional Transport Bridge)

FastMCP runs an HTTP server (Uvicorn) that speaks the MCP streamable HTTP transport natively. Optionally, a SLIM or NATS transport can be wired alongside HTTP for bridged access.

### Initialization flow

The FastMCP client initialization involves two HTTP POST requests that establish a session:

```
  Client                                 FastMCP Server (Uvicorn :8081)
    │                                              │
    │  POST / {"method": "initialize", ...}        │
    │─────────────────────────────────────────────>│
    │                                              │
    │  200 OK  +  Mcp-Session-Id: <session_id>     │
    │<─────────────────────────────────────────────│
    │                                              │
    │  POST / {"method": "notifications/initialized"}
    │  Mcp-Session-Id: <session_id>                │
    │─────────────────────────────────────────────>│
    │                                              │
    │  200 OK (session ready)                      │
    │<─────────────────────────────────────────────│
    │                                              │
    │  POST / {"method": "tools/list", ...}        │
    │  Mcp-Session-Id: <session_id>                │
    │─────────────────────────────────────────────>│
    │                                              │
```

For more details, refer to the [MCP transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#sequence-diagram).

### Server: `weather_server_fast.py`

```python
import asyncio
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_sessions import AppContainer
from mcp.server.fastmcp import FastMCP

# Create a FastMCP server instance
mcp = FastMCP()

# Add a tool
@mcp.tool()
async def get_forecast(location: str) -> str:
    """Fetch the weather forecast for a given location."""
    return "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n"

factory = AgntcyFactory()

async def main():
    # Option A: FastMCP with transport bridge (HTTP + SLIM)
    transport = factory.create_transport(
        "SLIM", endpoint="http://localhost:46357", name="default/default/weather_fast"
    )
    app_session = factory.create_app_session(max_sessions=1)
    app_container = AppContainer(
        mcp, transport=transport, topic="weather_agent.fastmcp"
    )

    # Option B: FastMCP HTTP-only (no transport bridge)
    # app_container = AppContainer(mcp)

    app_session.add_app_container("default_session", app_container)
    await app_session.start_all_sessions(keep_alive=True)

if __name__ == "__main__":
    asyncio.run(main())
```

Note: For FastMCP, pass the `mcp` instance directly (not `mcp._mcp_server`). The handler auto-detection selects `FastMCPServerHandler` for `FastMCP` types.

### Client: `weather_client_fast.py`

```python
from agntcy_app_sdk.factory import AgntcyFactory
import asyncio

factory = AgntcyFactory()

# Optional: create a transport for bridged access
transport = factory.create_transport(
    "SLIM", endpoint="http://localhost:46357", name="default/default/weather_client_fast"
)

async def main():
    # Create a FastMCP client — performs the HTTP initialization handshake,
    # then returns an MCPClient for point-to-point requests.
    client = await factory.fast_mcp().create_client(
        url="http://localhost:8081",
        topic="weather_agent.fastmcp",
        transport=transport,
    )

    async with client as mcp_client:
        tools = await mcp_client.list_tools()
        print("Available tools:", tools)

        result = await mcp_client.call_tool(
            name="get_forecast",
            arguments={"location": "Colombia"},
        )
        print("Forecast result:", result)

if __name__ == "__main__":
    asyncio.run(main())
```

### Default port configuration

The FastMCP server uses **port 8081** by default. Configure it via the `FAST_MCP_PORT` environment variable:

```bash
export FAST_MCP_PORT=9090
```

---

## Identity TBAC Integration

Activate Agntcy Identity Service TBAC by configuring the `IDENTITY_AUTH_ENABLED` and `IDENTITY_SERVICE_API_KEY` environment variable with the Identity App Service API key.
For more details, refer to the [official documentation](https://identity-docs.outshift.com/docs/dev#mcp-integration-using-the-python-sdk).

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
