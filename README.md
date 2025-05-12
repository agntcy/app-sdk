Agent Gateway SDK
========================
A multi-protocol gateway factory with variable transports and observability for agent-to-agent communication.

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

Create an A2A client:
```python
from gateway_sdk.factory import GatewayFactory

factory = GatewayFactory()

default_client = factory.create_client("A2A", "http://localhost:8080")
client_with_agp = factory.create_client("A2A", "http://localhost:8080", transport="AGP")
```

## Testing

Run a sample a2a server:
```bash
uv run tests/helloworld/__main__.p
```

Run test for a2a gateways
```bash
uv run pytest tests/test_a2a.py -s
```

## Roadmap
- [ ] Add support for transport decoupling and usage
- [ ] Add additional protocols
- [ ] Add observability
- [ ] Add authentication and transport security
- [ ] Add AGP control plane integration
- [ ] Add traffic routing via AGP control plane
