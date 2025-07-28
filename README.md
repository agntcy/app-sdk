<div align='center'>

<h1>
  Application SDK
</h1>

<a href="https://agntcy.org">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/_logo-Agntcy_White@2x.png" width="300">
    <img alt="" src="assets/_logo-Agntcy_FullColor@2x.png" width="300">
  </picture>
</a>

&nbsp;

</div>

The Agntcy Application SDK offers an interoperable factory for constructing multi-agent components as part of the emerging [internet of agents](https://outshift.cisco.com/the-internet-of-agents). The SDK factory will provide a single high-level interface to interact with Agntcy components such as [SLIM](https://github.com/agntcy/slim), [Observe-SDK](https://github.com/agntcy/observe/tree/main), and [Identity](https://github.com/agntcy/identity/tree/main), while enabling interoperability with agentic protocols such as A2A and MCP.

<div align='center'>
  
<pre>
✅ A2A over SLIM           ✅ A2A over NATS              🕐 A2A over MQTT             
✅ Request-reply           ✅ Publish-subscribe          ✅ Broadcast                 
✅ MCP client factory      🕐 Observability provider     🕐 Identity provider         
</pre>

<div align='center'>

[![PyPI version](https://img.shields.io/pypi/v/agntcy-app-sdk.svg)](https://pypi.org/project/agntcy-app-sdk/)
[![license](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/agntcy/app-sdk/LICENSE)

</div>
</div>
<div align="center">
  <div style="text-align: center;">
    <a target="_blank" href="#quick-start" style="margin: 0 10px;">Quick Start</a> •
    <a target="_blank" href="docs/USAGE_GUIDE.md" style="margin: 0 10px;">Usage Guide</a> •
    <a target="_blank" href="#reference-apps" style="margin: 0 10px;">Reference Apps</a> •
    <a target="_blank" href="#agntcy-component-usage" style="margin: 0 10px;">Agntcy Component Usage</a> •
    <a target="_blank" href="#contributing" style="margin: 0 10px;">Contributing</a>
  </div>
</div>

&nbsp;

# Quick Start

Install the SDK via pip:

```bash
pip install agntcy-app-sdk
```

Or install from source:

```bash
git clone https://github.com/agntcy/app-sdk.git
pip install -e app-sdk
```

The following examples demonstrate how to use the factory to create A2A servers, clients, and MCP clients with a SLIM or NATS transport.

[**A2A Server**](#a2a-server-with-transport-example): Create an A2A server bridge with a `SLIM` | `NATS` transport.  
[**A2A Client**](#a2a-client-with-transport-example): Create an A2A client with a `SLIM` | `NATS` transport.  
[**MCP Client**](#mcp-client-from-factory-example): Create an MCP client default `streamable-http` transport.

### A2A Server with Transport Example

```python
from a2a.server.apps import A2AStarletteApplication
from agntcy_app_sdk.factory import AgntcyFactory

# bring your own agent card and request handler
server = A2AStarletteApplication(
  agent_card=agent_card, http_handler=request_handler
)

factory = AgntcyFactory()
transport = factory.create_transport("SLIM", "http://localhost:46357")
bridge = factory.create_bridge(server, transport=transport)

await bridge.start()
```

### A2A Client with Transport Example

```python
from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.factory import ProtocolTypes

factory = AgntcyFactory()

transport = factory.create_transport("NATS", "localhost:4222")

# connect via agent URL
client_over_nats = await factory.create_client("A2A", agent_url="http://localhost:9999", transport=transport)

# or connect via agent topic
client_over_nats = await factory.create_client(ProtocolTypes.A2A.value, agent_topic="Hello_World_Agent_1.0.0", transport=transport)
```

### MCP Client from Factory Example

```python
from agntcy_app_sdk.factory import AgntcyFactory

# Create factory and transport
factory = AgntcyFactory()
transport_instance = factory.create_transport(
    transport="STREAMABLE_HTTP", endpoint="http://localhost:8123/mcp"
)

# Create MCP client
client = await factory.create_client(
    "MCP",
    agent_url=endpoint,
    transport=transport_instance,
)
```

For more details and exhaustive capabilities, see the [Usage Guide](docs/USAGE_GUIDE.md).

# Reference Apps

For fully functional distributed multi-agent examples, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy)!

# Agntcy Component Usage

### SLIM

SLIM (Secure Low-Latency Interactive Messaging) may be used to facilitate communication between AI agents with various communication patterns such as request-reply, publish-subscribe, and broadcast. The AgntcyFactory implements a high-level SLIM transport wrapper which is used to standardize integration with agntcy-app-sdk protocol implementations including A2A and MCP. For more details and usage guides for SLIM, see the [SLIM repository](https://github.com/agntcy/slim).

### Observe

The AgntcyFactory may be configured to use the Observe-SDK for multi-agentic application observability by setting the `enable_tracing` parameter to `True` when creating the factory instance. This will initialize an observe tracer and enable SLIM and A2A auto-instrumentation if necessary.

```
factory = AgntcyFactory(enable_tracing=True)
```

For more details and usage guides for Agntcy Observe, see the [Observe-SDK repository](https://github.com/agntcy/observe/tree/main)

### Identity (coming soon)

See the [Identity repository](https://github.com/agntcy/identity/tree/main) for more details.

# Testing

The `/tests` directory contains e2e tests for the factory, including A2A client and various transports.

### Prerequisites

Run the required message bus services:

```bash
docker-compose -f infra/docker/docker-compose.yaml up
```

**✅ Test the factory with A2A client and all available transports**

Run the parameterized e2e test for the A2A client across all transports:

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s
```

Or run a single transport test:

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s -k "SLIM"
```

# Contributing

Contributions are welcome! Please see the [contribution guide](CONTRIBUTING.md) for details on how to contribute to the Agntcy Application SDK.
