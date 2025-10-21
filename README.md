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

The Agntcy Application SDK offers an interoperable factory hub for constructing / instantiating multi-agent components as part of the emerging [internet of agents](https://outshift.cisco.com/the-internet-of-agents). The SDK factory will provide a single high-level interface to interact with Agntcy components such as [SLIM](https://github.com/agntcy/slim), [Observe-SDK](https://github.com/agntcy/observe/tree/main), and [Identity](https://github.com/agntcy/identity/tree/main), while enabling interoperability with agentic protocols such as A2A and MCP. The initial release of the Agntcy Application SDK focuses on this interoperability across agent protocols and message transports. It introduces a BaseTransport interface, with implementations for SLIM, NATS, and StreamableHTTP, and a BaseAgentProtocol interface, implemented by protocols such as A2A and MCP. These interfaces decouple protocol logic from transport, enabling flexible and extensible agent communication

<div align='center'>
  
<pre>
✅ A2A over SLIM           ✅ A2A over NATS              🕐 A2A over MQTT             
✅ Request-reply           ✅ Publish-subscribe          ✅ Broadcast                 
✅ MCP over SLIM           ✅ MCP over NATS              ✅ Observability provider       
🕐 Identity provider         
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
    <a target="_blank" href="#reference-application" style="margin: 0 10px;">Reference Application</a> •
    <a target="_blank" href="#agntcy-component-usage" style="margin: 0 10px;">Agntcy Component Usage</a> •
    <a target="_blank" href="#contributing" style="margin: 0 10px;">Contributing</a>
  </div>
</div>

&nbsp;

# Quick Start

Install the SDK via pip:

```bash
pip install agntcy-app-sdk
# or install via uv: uv add agntcy-app-sdk
```

Or install from source:

```bash
git clone https://github.com/agntcy/app-sdk.git
pip install -e app-sdk
```

Now we can list the registered protocols, transports, and observability providers in the factory:

```python
factory = AgntcyFactory()

protocols = factory.registered_protocols()
transports = factory.registered_transports()
observability_providers = factory.registered_observability_providers()

# ['A2A', 'MCP', 'FastMCP']
# ['SLIM', 'NATS', 'STREAMABLE_HTTP']
# ['ioa_observe']
```

Next, we can create a protocol client over a transport of choice using the factory:

[**MCP Client**](#mcp-client-from-factory-example): Create an MCP client with a `SLIM` | `NATS` transport.  
[**A2A Client**](#a2a-client-from-factory-example): Create an A2A client with a `SLIM` | `NATS` transport.

## MCP Client from Factory Example

```python
from agntcy_app_sdk.factory import AgntcyFactory

# Create factory and transport
factory = AgntcyFactory()
transport_instance = factory.create_transport(
    transport="SLIM", endpoint="http://localhost:46357", name="org/namespace/agent-foo"
)

# Create MCP client
mcp_client = factory.create_client(
    "MCP",
    agent_topic="my_remote_mcp_server",
    transport=transport_instance,
)
async with mcp_client as client:
  tools = await client.list_tools()
```

See the [MCP Usage Guide](docs/MCP_USAGE_GUIDE.md) for an end-to-end guide on using the MCP client and server with different transports.

### A2A Client from Factory Example

```python
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory()
transport = factory.create_transport("NATS", "localhost:4222")

# or connect via agent topic
client_over_nats = await factory.create_client("A2A", agent_topic="my_remote_a2a_server", transport=transport)
```

See the [A2A Usage Guide](docs/A2A_USAGE_GUIDE.md) for an end-to-end guide on using the A2A client and server with different transports.

# Reference Application

<a href="https://github.com/agntcy/coffeeAgntcy">
  <img alt="" src="assets/coffee_agntcy.png" width="284">
</a>

For a fully functional distributed multi-agent sample app, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy)!

# Agntcy Component Usage

### SLIM (0.6.0)

SLIM (Secure Low-Latency Interactive Messaging) may be used to facilitate communication between AI agents with various communication patterns such as request-reply, and moderated group-chat. The AgntcyFactory implements a high-level SLIM transport wrapper which is used to standardize integration with agntcy-app-sdk protocol implementations including A2A and MCP. For more details and usage guides for SLIM, see the [docs](https://docs.agntcy.org/messaging/slim-core/) and [repository](https://github.com/agntcy/slim).

### Observe (1.0.22)

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

**✅ Test the factory with FastMCP client and all available transports**

Run a single transport test for FastMCP:

```bash
uv run pytest tests/e2e/test_fast_mcp.py::test_client -s -k "SLIM"
```

Run a single transport test for concurrent FastMCP:

```bash
uv run pytest tests/e2e/test_concurrent_fast_mcp.py::test_client -s -k "SLIM"
```

## PyPI Release Flow

Publishing to PyPI is automated via GitHub Actions. To release a new version:

1. Update the `version` field in `pyproject.toml` to the desired release version.
2. Commit this change and merge it into the `main` branch via a pull request.
3. Ensure your local `main` is up to date:
   ```bash
   git checkout main
   git pull origin main
   ```
4. Create and push a tag from the latest `main` commit. The tag must be in the format `vX.Y.Z` and match the `pyproject.toml` version:
   ```bash
   git tag -a v0.2.6 -m "Release v0.2.6"
   git push origin v0.2.6
   ```
5. The release workflow will validate the tag and version, then publish to PyPI if all checks pass.

**Note:** Tags must always be created from the `main` branch and must match the version in `pyproject.toml`.

# Contributing

Contributions are welcome! Please see the [contribution guide](CONTRIBUTING.md) for details on how to contribute to the Agntcy Application SDK.
