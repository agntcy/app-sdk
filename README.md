<div align='center'>

<h2>
  Agntcy Application SDK
</h2>

&nbsp;

</div>

The Agntcy Application SDK provides a factory and set of interfaces for creating agentic communication bridges and clients. This SDK is designed to enable interoperability between different agent protocols and messaging layers by decoupling protocol logic from the underlying network stack.

&nbsp;

<div align='center'>
  
<pre>
✅ A2A over NATS             ✅ A2A over SLIM              🕐 A2A over MQTT
✅ Request-reply messaging   ✅ Publish-subscribe          ✅ Broadcast messaging
✅ MCP transport decoupling  🕐 Baked-in observability     🕐 Baked-in identity & trust
</pre>

<div align='center'>

[![PyPI version](https://img.shields.io/pypi/v/ioa-observe-sdk.svg)](https://pypi.org/project/gateway-sdk/)
[![license](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/cisco-outshift-ai-agents/gateway-sdk/LICENSE)

</div>
</div>
<div align="center">
  <div style="text-align: center;">
    <a target="_blank" href="#quick-start" style="margin: 0 10px;">Quick start</a> •
    <a target="_blank" href="#featured-examples" style="margin: 0 10px;">Examples</a> •
    <a target="_blank" href="#features" style="margin: 0 10px;">Features</a> •
    <a target="_blank" href="#api-reference" style="margin: 0 10px;">API Reference</a>
  </div>
</div>

&nbsp;

# Quick start

---

### Architecture

[![architecture](assets/architecture.png)]()

## Installation

This project uses [uv](https://github.com/astral-sh/uv) for package management:

```bash
# Install UV if you don't have it already
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a new virtual environment and install the dependencies:

```bash
uv venv
source .venv/bin/activate
```

## Getting Started

| Protocol \ Transport | SLIM | NATS | STREAMABLE_HTTP | MQTT |
| -------------------- | :--: | :--: | :-------------: | :--: |
| **A2A**              |  ✅  |  ✅  |       🕐        |  🕐  |
| **MCP**              |  🕐  |  🕐  |       ✅        |  🕐  |

Create an A2A server bridge with your network transport of choice:

```python
from a2a.server import A2AServer
from agntcy_app_sdk.factory import GatewayFactory

...
server = A2AServer(agent_card=agent_card, request_handler=request_handler)

factory = GatewayFactory()
transport = factory.create_transport("NATS", "localhost:4222")
bridge = factory.create_bridge(server, transport=transport)

await bridge.start()
```

Create an A2A client with a transport of your choice:

```python
from agntcy_app_sdk.factory import GatewayFactory
from agntcy_app_sdk.factory import ProtocolTypes

factory = GatewayFactory()

transport = factory.create_transport("NATS", "localhost:4222")

# connect via agent URL
client_over_nats = await factory.create_client("A2A", agent_url="http://localhost:9999", transport=transport)

# or connect via agent topic
client_over_nats = await factory.create_client(ProtocolTypes.A2A.value, agent_topic="Hello_World_Agent_1.0.0", transport=transport)
```

## Testing

The `/tests` directory contains e2e tests for the gateway factory, including A2A client and various transports.

### Prerequisites

Run the required message bus services:

```bash
docker-compose -f infra/docker/docker-compose.yaml up
```

**✅ Test the gateway factory with A2A client and all available transports**

Run the parameterized e2e test for the A2A client across all transports:

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s
```

Or run a single transport test:

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s -k "SLIM"
```

## Development

Run a local documentation server:

```bash
make docs
```

## Roadmap

TBD
