Agent Gateway SDK
========================
A factory package designed to simplify agent communication across various protocols and network transports. It enables interoperability between agent protocols and various messaging layers by decoupling protocol logic from the underlying network stack. In addition to streamlined communication setup, the package offers built-in, end-to-end observability via OpenTelemetry.

<div align="center" style="margin-bottom: 1rem;">
  <a href="https://pypi.org/project/your-package-name/" target="_blank" style="margin-right: 0.5rem;">
    <img src="https://img.shields.io/pypi/v/your-package-name?logo=pypi&logoColor=%23FFFFFF&label=Version&color=%2300BCEB" alt="PyPI version">
  </a>
  <a href="./LICENSE" target="_blank">
    <img src="https://img.shields.io/badge/License-Apache%202.0-blue?color=%2300BCEB" alt="Apache License">
  </a>
</div>

---

**🧠 Supported Agent Protocols**  

- [x] A2A

**📡 Supported Messaging Transports**  

- [x] NATS  
- [ ] AGP *(coming soon)*  
- [ ] MQTT *(coming soon)*  


### Architecture

[![architecture](assets/architecture.png )]()

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

Create an A2A server bridge with your network transport of choice:
```python
from a2a.server import A2AServer
from gateway_sdk.factory import GatewayFactory

...
server = A2AServer(agent_card=agent_card, request_handler=request_handler)

factory = GatewayFactory()
transport = factory.create_transport("NATS", "localhost:4222", options={})
bridge = factory.create_bridge(server, transport=transport)

await bridge.start()
```

Create an A2A client with a transport of your choice:
```python
from gateway_sdk.factory import GatewayFactory
from gateway_sdk.factory import ProtocolTypes

factory = GatewayFactory()

transport = factory.create_transport("NATS", "localhost:4222", options={})
    
# connect via agent URL
client_over_nats = await factory.create_client("A2A", agent_url="http://localhost:9999", transport=transport)

# or connect via agent topic
client_over_nats = await factory.create_client(ProtocolTypes.A2A.value, agent_topic="Hello_World_Agent_1.0.0", transport=transport)
```

## Testing

**✅ Test the gateway factory with default A2A client/server**

Run a sample agent via an A2A server:
```bash
uv run python tests/helloworld/__main__.py 
```

In a second terminal, run an A2A test client:
```bash
uv run pytest tests/test_a2a.py::test_a2a_factory_client -s
```

**🚀 Test the gateway factory with A2A over NATS transport**

Run a Nats server and observability stack:
```bash
uv run python gateway_cli/cli.py up
```

Run an A2A server with a NATS bridge:
```bash
uv run python tests/helloworld/__bridge__.py
```

In a second terminal, run an A2A test client with a NATS transport:
```bash
uv run pytest tests/test_a2a.py::test_a2a_factory_client_with_transport -s
```

Run an A2A test client, connecting via a Card topic instead of an agent URL:  
```bash
uv run pytest tests/test_a2a.py::test_a2a_factory_client_from_topic -s
```

**🔎 View the agent traces from the Jaeger UI**

Navigate to `http://localhost:16686`

[![jaeger](assets/trace.png )]()

## Development
Run a local documentation server:
```bash
make docs
```

## Roadmap
- [x] Support A2A protocol
- [x] Support NATS transport
- [ ] Support AGP transport
- [ ] Support MQTT transport
- [x] Support e2e observability via Traceloop and OpenTelemetry
- [ ] Add authentication and transport security
- [ ] Add traffic routing via AGP control plane