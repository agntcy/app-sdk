<div align='center'>

<h1>Agntcy Application SDK</h1>

<a href="https://agntcy.org">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/_logo-Agntcy_White@2x.png" width="300">
    <img alt="Agntcy Logo" src="assets/_logo-Agntcy_FullColor@2x.png" width="300">
  </picture>
</a>

<p><i>Build interoperable multi-agent systems for the Internet of Agents</i></p>

[![PyPI version](https://img.shields.io/pypi/v/agntcy-app-sdk.svg)](https://pypi.org/project/agntcy-app-sdk/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://github.com/agntcy/app-sdk/LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)

</div>
<div align="center">
  <div style="text-align: center;">
    <a target="_blank" href="#quick-start" style="margin: 0 10px;">Quick Start</a> •
    <a target="_blank" href="docs/API_REFERENCE.md" style="margin: 0 10px;">API Reference</a> •
    <a target="_blank" href="#reference-application" style="margin: 0 10px;">Reference Application</a> •
    <a target="_blank" href="#agntcy-component-usage" style="margin: 0 10px;">Agntcy Component Usage</a> •
    <a target="_blank" href="#contributing" style="margin: 0 10px;">Contributing</a>
  </div>
</div>

</div>

## Overview

The Agntcy Application SDK provides a unified factory interface for building interoperable, multi-agent components. It defines standard abstractions and interoperability layers that connect Agntcy and open-source transports, protocols, and directories—enabling agents to communicate and coordinate seamlessly.

It is intended as an integration and learning surface primarily used in [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy), not as a canonical or prescriptive SDK for AGNTCY subsystems. Standards and reference guidance remain the responsibility of the working groups. Please see the respective components for their latest SDK or interfaces.

### Features

<table>
<tr>
<td width="25%" valign="top">

**🔌 Semantic Layer**

- A2A over abstract transport
- MCP over abstract transport
- Experimental agentic communication patterns

</td>
<td width="25%" valign="top">

**🚀 Transport Layer**

- SLIM transport setup
- NATS transport setup
- Point-to-point messaging
- Pub-sub messaging
- Group chat messaging

</td>
<td width="25%" valign="top">

**📂 Directory 🕐**

- Agntcy Directory integration
- Git-based directory
- Agent registry
- Agent discovery

</td>
<td width="25%" valign="top">

**🔐 Identity 🕐**

- Agent badge creation
- Agent badge verification
- Tool-based access control
- Task-based access control

</td>
</tr>
<tr>
<td colspan="4" align="center">

**🔍 Observability** • Built-in Agntcy Observe SDK integration

</td>
</tr>
</table>

## 📦 Installation

```bash
# Install via pip
pip install agntcy-app-sdk

# Or use uv for faster installs
uv add agntcy-app-sdk

# Install from source
git clone https://github.com/agntcy/app-sdk.git
pip install -e app-sdk
```

# Quick Start

### Explore Available Components

```python
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory()

print(factory.registered_protocols())              # ['A2A', 'MCP', 'FastMCP']
print(factory.registered_transports())             # ['SLIM', 'NATS', 'STREAMABLE_HTTP']
print(factory.registered_observability_providers()) # ['ioa_observe']
```

### Create an MCP Client

```python
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory()

# Initialize transport
transport = factory.create_transport(
    transport="SLIM",
    endpoint="http://localhost:46357",
    name="org/namespace/agent-foo"
)

# Create and use MCP client
mcp_client = factory.create_client(
    "MCP",
    agent_topic="my_remote_mcp_server",
    transport=transport
)

async with mcp_client as client:
    tools = await client.list_tools()
    # Your agent logic here
```

**📖 [View complete MCP guide →](docs/MCP_USAGE_GUIDE.md)**

### Create an A2A Client

```python
from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory()
transport = factory.create_transport("NATS", "localhost:4222")

# Connect to remote A2A server
client = await factory.create_client(
    "A2A",
    agent_topic="my_remote_a2a_server",
    transport=transport
)
```

**📖 [View complete A2A guide →](docs/A2A_USAGE_GUIDE.md)**

## 📁 Project Structure

```
📁 src/
└── 📦 agntcy_app_sdk/
    ├── 🏭 factory.py            # Main factory interface
    ├── 🔄 app_sessions.py       # Session management
    ├── 📂 directory/            # Agent directory services
    ├── 🔐 identity/             # Authentication & identity
    ├── 🧠 semantic/             # Semantic layer (SLIM)
    ├── 🌐 transport/            # Transport implementations
    └── 🛠️  common/              # Shared utilities
```

# Reference Application

<a href="https://github.com/agntcy/coffeeAgntcy">
  <img alt="" src="assets/coffee_agntcy.png" width="284">
</a>

For a fully functional distributed multi-agent sample app, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy)!

# Agntcy Component Usage

<a href="https://github.com/agntcy/coffeeAgntcy">
  <img alt="Agntcy App SDK Architecture" src="assets/app-sdk-arch.jpg" width="600">
</a>

| Component       | Version       | Description                                                                                                                                                                                | Repo                                                 |
| --------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------- |
| **SLIM**        | `1.0.0`       | Secure Low-Latency Interactive Messaging (SLIM) facilitates communication between AI agents using request-reply and moderated group-chat patterns.                                         | [Repo](https://github.com/agntcy/slim)               |
| **Observe SDK** | `1.0.34`      | Enables multi-agent observability by setting `enable_tracing=True` when initializing the `AgntcyFactory`. This automatically configures tracing and auto-instrumentation for SLIM and A2A. | [Repo](https://github.com/agntcy/observe/tree/main)  |
| **Directory**   | _Coming soon_ | Component for service discovery and directory-based agent lookups.                                                                                                                         | [Repo](https://github.com/agntcy/dir)                |
| **Identity**    | _Coming soon_ | Provides agent identity, authentication, and verification mechanisms.                                                                                                                      | [Repo](https://github.com/agntcy/identity/tree/main) |

# Testing

The `/tests` directory contains both unit and end-to-end (E2E) tests for Agntcy components and workflows.

## Prerequisites

Before running tests, start the required message bus services:

```bash
docker-compose -f services/docker/docker-compose.yaml up
```

## Running Tests

### 🧩 A2A Client Tests

**Run all transports**

Run the parameterized E2E test for the A2A client across all supported transports:

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s
```

**Run a single transport**

To test only a specific transport (e.g. SLIM):

```bash
uv run pytest tests/e2e/test_a2a.py::test_client -s -k "SLIM"
```

**Broadcast messaging**

Run the E2E test for A2A broadcast communication across all transports:

```bash
uv run pytest tests/e2e/test_a2a.py::test_broadcast -s
```

**Group chat**

Run the E2E test for A2A moderated group-chat using a specific transport (e.g. SLIM):

```bash
uv run pytest tests/e2e/test_a2a.py::test_groupchat -s -k "SLIM"
```

### FastMCP Client Tests

**Single transport**

Run an E2E test for the FastMCP client with a specific transport:

```bash
uv run pytest tests/e2e/test_fast_mcp.py::test_client -s -k "SLIM"
```

# Contributing

Contributions are welcome! Please see the [contribution guide](CONTRIBUTING.md) for details on how to contribute to the Agntcy Application SDK.

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
