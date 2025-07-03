<div align='center'>

<h1>
  Agntcy Application SDK
</h1>

</div>

for the internet of agents

The Agntcy Application SDK provides a factory and set of interfaces for creating agentic communication bridges and clients. This SDK is designed to enable interoperability between different agent protocols and messaging layers by decoupling protocol logic from the underlying network stack.

&nbsp;

<div align='center'>
  
<pre>
✅ A2A over SLIM           ✅ A2A over NATS              🕐 A2A over MQTT (soon)      
✅ Request-reply messaging ✅ Publish-subscribe          ✅ Broadcast messaging       
✅ MCP client factory      🕐 Observability built-in     🕐 Identity & trust built-in 
</pre>

<div align='center'>

[![PyPI version](https://img.shields.io/pypi/v/ioa-observe-sdk.svg)](https://pypi.org/project/gateway-sdk/)
[![license](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/cisco-outshift-ai-agents/gateway-sdk/LICENSE)

</div>
</div>
<div align="center">
  <div style="text-align: center;">
    <a target="_blank" href="#quick-start" style="margin: 0 10px;">Quick Start</a> •
    <a target="_blank" href="#api-reference" style="margin: 0 10px;">API Reference</a> •
    <a target="_blank" href="#reference-application" style="margin: 0 10px;">Reference Apps</a> •
    <a target="_blank" href="#testing" style="margin: 0 10px;">Testing</a> •
    <a target="_blank" href="#contributing" style="margin: 0 10px;">Contributing</a>
  </div>
</div>

&nbsp;

# Quick Start

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
client_over_nats = await factory.create_client("A2A", transport=transport)

# or connect via agent topic
client_over_nats = await factory.create_client(ProtocolTypes.A2A.value, agent_topic="Hello_World_Agent_1.0.0", transport=transport)
```

For more details and exhaustive capabilities, see the [API Reference](#api-reference) below.

# API Reference

# Reference Applications

# Testing

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

# Contributing
