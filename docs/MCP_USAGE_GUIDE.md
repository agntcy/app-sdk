# MCP Usage Guide

In this guide, we will walk through some of the key features of the Agntcy Application SDK and explore an end-to-end example of creating two MCP agents that communicate over a custom transport (SLIM, NATS).

The following diagram illustrates how the MCP protocol maps to a transport implementation:

<p align="center">
  <img src="mcp-architecture.jpg" alt="architecture" width="90%">
</p>

The following table summarizes the current MCP and transport support in the Agntcy Application SDK:

| Protocol \ Transport | SLIM | NATS | MQTT |
| -------------------- | :--: | :--: | :--: |
| **MCP**              |  ✅  |  ✅  |  🕐  |

### ⚡️ Connecting an MCP client to an MCP server over an abstract transport (SLIM | NATS)

A benefit of decoupling protocols from transports is that you can easily create agents that communicate over non http, point-to-point transports such as NATS or Agntcy's SLIM. Below is an example of how to create an MCP client and server that communicate over SLIM's gateway server.

We will use `uv` for package management and virtual environments. If you don't have it installed, you can install it via:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a new project directory:

```bash
uv init agntcy-mcp
cd agntcy-mcp
```

Install the Agntcy Application SDK and Langgraph:

```bash
uv add agntcy-app-sdk
```

Next we will create a simple weather MCP server that responds to weather queries. Create a file named `weather_server.py` and implement the MCP server and add a message bridge to a SLIM transport:

```python
from agntcy_app_sdk.factory import AgntcyFactory
from mcp.server.fastmcp import FastMCP
import asyncio

# create an MCP server instance
mcp = FastMCP()

# add a tool to the MCP server
@mcp.tool()
async def get_forecast(location: str) -> str:
    return "Temperature: 30°C\n" "Humidity: 50%\n" "Condition: Sunny\n"

# initialize the Agntcy factory
factory = AgntcyFactory()

# create an Agntcy factory transport instance
transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")
# transport = factory.create_transport("NATS", endpoint="localhost:4222")

async def main():
    # serve the MCP server via a message bridge
    bridge = factory.create_bridge(mcp, transport=transport, topic="my_weather_agent.mcp")
    await bridge.start(blocking=True)

if __name__ == "__main__":
    asyncio.run(main())
```

Next we will create a simple client that queries the weather server. Create a file named `weather_client.py` and request an MCP client via the Agntcy factory:

```python
from agntcy_app_sdk.factory import AgntcyFactory
import asyncio

factory = AgntcyFactory()
transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")
# transport = factory.create_transport("NATS", endpoint="localhost:4222")

async def main():
    # Create a MCP client
    mcp_client = factory.create_client(
        "MCP",
        agent_topic="my_weather_agent.mcp",
        transport=transport,
    )
    async with mcp_client as client:
        # Build message request
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

A few notes about the code above:

- The weather server is not binding to a host and port as the MCP server typically would, rather it is listening on a provided topic via the transport. In the current release, both the clent and server must know of and use the same topic to communicate.

### 🏁 Running the Example

First lets run the SLIM transport server, see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/infra/docker/docker-compose.yaml) or SLIM [repo](https://github.com/agntcy/slim/tree/main).

Now we can run the weather agent server:

```bash
uv run python weather_server.py
```

You should see a log message indicating that the message bridge is running:

```
2025-08-07 09:43:02 [agntcy_app_sdk.transports.slim.transport] [INFO] [subscribe:168] Subscribed to default/default/my_weather_agent.mcp
```

Next, we can run the weather client:

```bash
uv run python weather_client.py
```

You should see a print output with the weather report:

```
Tool call result: meta=None content=[TextContent(type='text', text='Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n', annotations=None, meta=None)] structuredContent={'result': 'Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n'} isError=False
```

🚀 Congratulations! You have successfully created an MCP client and server that communicate over SLIM via the agntcy-app-sdk SLIM transport.

For a fully functional multi-agent example integrating A2A, Agntcy, and Langgraph, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy).

### ✨ FastMCP Support (Updated: 2025/08/18)

The Agntcy Application SDK now fully supports FastMCP, an enhanced version of the MCP protocol designed for high-performance and concurrent operations. FastMCP introduces additional capabilities such as streamable HTTP transport and optimized initialization flows. Developers can leverage these features seamlessly using the `FastMCPProtocol`.

#### Initialization Flow

The FastMCP client initialization involves two POST requests:
1. **Initialization Request**: Establishes the session and retrieves the `Mcp-Session-Id`.
2. **Notification Request**: Confirms the session initialization.

The following diagram illustrates the initialization flow:

<p align="center">
  <img src="./fast-mcp-client-init.png" alt="FastMCP Client Initialization Flow" width="90%">
</p>

For more details, refer to the [official documentation](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#sequence-diagram).

#### Example: Creating and Running a FastMCP Server

```python
import asyncio
from agntcy_app_sdk.factory import AgntcyFactory
from mcp.server.fastmcp import FastMCP

# Create an MCP server instance
mcp = FastMCP()

# Add a tool to the MCP server
@mcp.tool()
async def get_forecast(location: str) -> str:
    """
    Tool to fetch the weather forecast for a given location.

    :param location: The location for which the forecast is requested.
    :return: A string containing the weather forecast.
    """
    return "Temperature: 30°C\nHumidity: 50%\nCondition: Sunny\n"

# Initialize the Agntcy factory
factory = AgntcyFactory()

async def main():
    # Create the transport instance
    transport_type = "SLIM"  # Replace with "NATS" if using NATS transport
    endpoint = "http://localhost:46357"  # Replace with your transport endpoint
    transport = factory.create_transport(transport_type, endpoint=endpoint)
    print(f"[setup] Transport created: {transport_type} | Endpoint: {endpoint}")

    # Create the bridge between MCP and transport
    bridge = factory.create_bridge(mcp, transport=transport, topic="test_topic.mcp")
    print("[setup] Bridge created with topic: test_topic.mcp")

    # Start the bridge
    print("[start] Starting the bridge...")
    await bridge.start(blocking=True)

if __name__ == "__main__":
    asyncio.run(main())
```

#### Default Port Configuration

The FastMCP server uses **port 8081** by default to avoid conflicts with other common ports like 8000. However, you can configure the port by setting the `FAST_MCP_PORT` environment variable.

For example, to change the port to 9090, you can run:

```bash
export FAST_MCP_PORT=9090
```

Then, start your server as usual. The server will now listen on port 9090.




#### Example: Creating a FastMCP Client

The `FastMCPProtocol.create_client` method allows you to create an MCP client for interacting with a FastMCP server. Below is an example demonstrating its usage, including the `route_path` parameter.

```python
from agntcy_app_sdk.protocols.fast_mcp.protocol import FastMCPProtocol
from agntcy_app_sdk.factory import AgntcyFactory
import asyncio

async def main():
    protocol = FastMCPProtocol()
    slim_transport = AgntcyFactory().create_transport("SLIM", endpoint="http://localhost:46357")

    # Create a FastMCP client
    client = await protocol.create_client(
        url="http://localhost:8081",
        topic="weather_agent.fastmcp",
        transport=slim_transport,  # Optional transport instance
        route_path="/custom-path",  # Custom route path for the client
        auth="your-bearer-token",  # Optional authentication
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

#### Key Notes:
- **`route_path`**: Specifies the custom route path for the client. If not provided, it defaults to `/`.
- **Authentication**: Supports both bearer tokens and `httpx.Auth` instances for flexible authentication.
- **Initialization Flow**: The client performs two POST requests to initialize the MCP connection, ensuring proper session setup.

For more details, refer to the [FastMCP API Reference](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports#sequence-diagram).

### ⚙️ Contributing additional Transports

To contribute a new transport implementation, follow these steps:

1. **Implement the Transport Interface**: Create a new class for your transport in the `src/agntcy_app_sdk/transports` directory. Ensure it inherits from the `BaseTransport` interface and implements all required methods.

2. **Update the Factory**: Modify the `AgntcyFactory` to include your new transport in the `create_transport` method.

3. **Add Tests**: Create unit tests for your transport in the `tests/e2e` directory. Ensure all tests pass.

4. **Documentation**: Update the documentation to include your new transport. This includes any relevant sections in the README and API reference.

5. **Submit a Pull Request**: Once your changes are complete, submit a pull request for review.

See [API Reference](API_REFERENCE.md) for detailed SDK API documentation.
