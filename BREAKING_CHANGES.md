# Breaking Changes

## Refactor: Split `BaseAgentProtocol` into `ServerHandler` + `ClientFactory`

### Removed

- **`BaseAgentProtocol`** (`semantic/base.py`) — removed entirely. Protocol classes (`A2AProtocol`, `MCPProtocol`, `FastMCPProtocol`) no longer inherit from a shared ABC; they are standalone internal classes.
- **`AgntcyFactory.create_protocol()`** — removed. Use `create_client()` (which now delegates to `ClientFactory` instances) or the handler system for server-side wiring.
- **`AgntcyFactory._protocol_registry`** — replaced by `_client_factory_registry`.
- **`AgntcyFactory.register_protocol()`** — removed (was a classmethod decorator).

### Changed

- **`AppContainer`** — constructor signature changed from `AppContainer(server, transport=..., topic=..., directory=...)` to `AppContainer(handler: ServerHandler)`. All transport wiring logic moved into `ServerHandler.setup()`.
- **`AppSession.add_app_container()`** — still works (deprecated shim) but the preferred API is the fluent builder: `session.add(server).with_transport(...).with_topic(...).with_session_id(...).build()`.
- **`AgntcyFactory.create_client()`** — now `async`. Callers must `await` it. It delegates to `ClientFactory` instances instead of `BaseAgentProtocol` instances.

### Added

- **`ServerHandler`** ABC (`semantic/base.py`) — server-side handler that owns server, transport, topic, and directory. Subclasses: `A2AServerHandler`, `MCPServerHandler`, `FastMCPServerHandler`.
- **`ClientFactory`** ABC (`semantic/base.py`) — standalone client factory per protocol. Subclasses: `A2AClientFactory`, `MCPClientFactory`, `FastMCPClientFactory`.
- **`ContainerBuilder`** (`app_sessions.py`) — fluent API for constructing `AppContainer` instances.
- **`AppSession.add(server)`** — entry point for the fluent builder API.
- Handler auto-detection: `ContainerBuilder.build()` resolves the correct `ServerHandler` subclass from the server type (`A2AStarletteApplication` → `A2AServerHandler`, `MCPServer` → `MCPServerHandler`, `FastMCP` → `FastMCPServerHandler`).

### Migration Guide

**Before (old API):**
```python
from agntcy_app_sdk.app_sessions import AppContainer

transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")
app_container = AppContainer(server, transport=transport, topic="my_topic")
app_session.add_app_container("session_id", app_container)
await app_session.start_all_sessions(keep_alive=True)
```

**After (new API):**
```python
transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")
app_session.add(server).with_transport(transport).with_topic("my_topic").with_session_id("session_id").build()
await app_session.start_all_sessions(keep_alive=True)
```

**Client creation (unchanged for callers):**
```python
client = await factory.create_client("A2A", agent_url="http://localhost:9999")
```
