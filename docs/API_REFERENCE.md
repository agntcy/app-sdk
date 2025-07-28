# API Reference

This document provides an overview of the main factory methods and utilities available in the Agntcy Application SDK.

## Enums

### `ProtocolTypes`

An `Enum` class defining supported agent protocol types.

```python
from agntcy_app_sdk import ProtocolTypes

ProtocolTypes.A2A  # "A2A"
ProtocolTypes.MCP  # "MCP"
```

#### Members:

- `A2A`: `"A2A"` – A2A protocol type.
- `MCP`: `"MCP"` – MCP protocol type.

---

### `TransportTypes`

An `Enum` class defining supported transport types.

```python
from agntcy_app_sdk import TransportTypes

TransportTypes.NATS  # "NATS"
TransportTypes.SLIM  # "SLIM"
```

#### Members:

- `A2A`: `"A2A"` – A2A transport.
- `SLIM`: `"SLIM"` – SLIM transport.
- `NATS`: `"NATS"` – NATS transport.
- `MQTT`: `"MQTT"` – MQTT transport.
- `STREAMABLE_HTTP`: `"StreamableHTTP"` – HTTP transport supporting streaming.

---

## `AgntcyFactory`

Factory class to create agent transport clients, bridges, and protocol handlers.

```python
from agntcy_app_sdk import AgntcyFactory

factory = AgntcyFactory(enable_tracing=True)
```

### Constructor

```python
AgntcyFactory(enable_tracing: bool = False)
```

- `enable_tracing` (bool): Enable or disable tracing. Default is `False`.

---

### `create_client`

```python
factory.create_client(
    protocol: str,
    agent_url: str | None = None,
    agent_topic: str | None = None,
    transport: BaseTransport | None = None,
    **kwargs
) -> BaseAgentClient
```

Creates an agent client using a specific protocol and transport.

**Arguments:**

- `protocol` (`str`): The protocol name (e.g., `"A2A"`).
- `agent_url` (`str | None`): Optional URL to the agent.
- `agent_topic` (`str | None`): Optional topic for agent communication.
- `transport` (`BaseTransport | None`): An optional transport instance.
- `**kwargs`: Additional protocol-specific parameters.

**Returns:**

- An instance of the client created by the protocol.

**Raises:**

- `ValueError` if both `agent_url` and `agent_topic` are missing.

---

### `create_bridge`

```python
factory.create_bridge(
    server: A2AStarletteApplication,
    transport: BaseTransport,
    topic: str | None = None
) -> MessageBridge
```

Creates a message bridge/receiver for a given server and transport.

**Arguments:**

- `server`: An instance of a supported application (e.g., `A2AStarletteApplication`).
- `transport` (`BaseTransport`): The transport layer used to receive messages.
- `topic` (`str | None`): Optional topic to subscribe to. Auto-generated if not provided for A2A.

**Returns:**

- A `MessageBridge` instance configured for the given server and transport.

**Raises:**

- `ValueError` for unsupported server types.

---

### `create_transport`

```python
factory.create_transport(
    transport: str,
    client: Any = None,
    endpoint: str = None
) -> BaseTransport
```

Instantiates a transport instance from a string name or a client/endpoint.

**Arguments:**

- `transport` (`str`): Name of the transport (e.g., `"NATS"`).
- `client` (`Any`): Optional transport-specific client instance.
- `endpoint` (`str`): Optional endpoint string used for configuration.

**Returns:**

- An instance of `BaseTransport` or subclass.

**Raises:**

- `ValueError` if both `client` and `endpoint` are missing.

---

### `create_protocol`

```python
factory.create_protocol(protocol: str) -> BaseAgentProtocol
```

Instantiates a protocol handler by name.

**Arguments:**

- `protocol` (`str`): Name of the protocol (e.g., `"A2A"` or `"MCP"`).

**Returns:**

- A new instance of the protocol class.

**Raises:**

- `ValueError` if the protocol type is unregistered.
