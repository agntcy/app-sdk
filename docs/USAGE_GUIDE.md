# Usage Guide

In this guide, we will walk through some of the key features of the Agntcy Application SDK and explore an end-to-end example of creating two A2A agents that communicate over a PubSub transport.

### 🌟 SDK Features and Components

The initial release of the Agntcy Application SDK focuses on interoperability across agent protocols and message transports. It introduces a BaseTransport interface, with implementations for SLIM, NATS, and StreamableHTTP, and a BaseAgentProtocol interface, implemented by protocols such as A2A and MCP. These interfaces decouple protocol logic from transport, enabling flexible and extensible agent communication. The following diagram illustrates how the A2A protocol maps to a transport implementation:

<p align="center">
  <img src="architecture.png" alt="architecture" width="80%">
</p>

The following table summarizes the current transport and protocol implementations available in the SDK:

| Protocol \ Transport | SLIM | NATS | STREAMABLE_HTTP | MQTT |
| -------------------- | :--: | :--: | :-------------: | :--: |
| **A2A**              |  ✅  |  ✅  |       🕐        |  🕐  |
| **MCP**              |  🕐  |  🕐  |       ✅        |  🕐  |

Additional features incorporating AGNTCY's identity and observability components are coming soon.

### ⚡️ Connecting two Agents over PubSub

A benefit of decoupling protocols from transports is that you can easily create agents that communicate over non http, point-to-point transports such as NATS or Agntcy's SLIM. Below is an example of how to create two A2A agents that communicate over SLIM's PubSub gateway.

We will use `uv` for package management and virtual environments. If you don't have it installed, you can install it via:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Create a new project directory:

```bash
uv init a2a-pubsub
cd a2a-pubsub
```

Install the Agntcy Application SDK and Langgraph:

```bash
uv add agntcy-app-sdk
```

Next we will create a simple weather agent that responds to weather queries. Create a file named `weather_agent.py` and implement the A2A agent and add a message bridge to a SLIM transport:

```python
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from agntcy_app_sdk.factory import AgntcyFactory

"""
Create the AgentSkill and AgentCard for a simple weather report agent.
"""

skill = AgentSkill(
    id="weather_report",
    name="Returns weather report",
    description="Provides a simple weather report",
    tags=["weather", "report"],
    examples=["What's the weather like?", "Give me a weather report"],
)

agent_card = AgentCard(
    name="Weather Agent",
    description="An agent that provides weather reports",
    url="",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],
    supportsAuthenticatedExtendedCard=False,
)

"""
Create the actual agent logic and executor.
"""

class WeatherAgent:
    """A simple agent that returns a weather report."""
    async def invoke(self) -> str:
        return "The weather is sunny with a high of 75°F."

class WeatherAgentExecutor(AgentExecutor):
    """Test AgentProxy Implementation."""

    def __init__(self):
        self.agent = WeatherAgent()

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
        result = await self.agent.invoke()
        event_queue.enqueue_event(new_agent_text_message(result))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")

"""
Create the A2A server and transport bridge to server the Weather Agent.
"""

async def main():
    # create an app-sdk factory to create the transport and bridge
    factory = AgntcyFactory()

    request_handler = DefaultRequestHandler(
        agent_executor=WeatherAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")
    bridge = factory.create_bridge(server, transport=transport)
    await bridge.start(blocking=True)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

Next we will create a simple client agent that queries the weather agent. Create a file named `weather_client.py` and implement the A2A client with a SLIM transport:

```python
from a2a.types import (
    SendMessageRequest,
    MessageSendParams,
    Message,
    Part,
    TextPart,
    Role,
)

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.factory import ProtocolTypes
from agntcy_app_sdk.protocols.a2a.protocol import A2AProtocol
from weather_agent import agent_card

factory = AgntcyFactory()
transport = factory.create_transport("SLIM", endpoint="http://localhost:46357")

async def main():
    # create an app-sdk factory to create the transport and bridge
    factory = AgntcyFactory()

    a2a_topic = A2AProtocol.create_agent_topic(agent_card)

    # create a client to connect to the A2A server
    client = await factory.create_client(ProtocolTypes.A2A.value, agent_topic=a2a_topic, transport=transport)

    message = "Hello, Weather Agent, how is the weather?"
    request = SendMessageRequest(
        params=MessageSendParams(
            message=Message(
                messageId="0",
                role=Role.user,
                parts=[Part(TextPart(text=message))],
            ),
        )
    )

    # send a message to the agent
    response = await client.send_message(request)
    print(response)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

A few notes about the code above:

- The weather agent is choosing not to provide a URL in its agent card, instead it will be discovered by a SLIM transport topic.
- Conversely, the client agent uses the `A2AProtocol.create_agent_topic` method to create a topic based on the agent` card, which is used to connect to the weather agent.

### 🏁 Running the Example

First lets run the SLIM transport server, see the agntcy-app-sdk [docker-compose.yaml](https://github.com/agntcy/app-sdk/blob/main/infra/docker/docker-compose.yaml) or SLIM [repo](https://github.com/agntcy/slim/tree/main).

Now we can run the weather agent server:

```bash
uv run python weather_agent.py
```

You should see a log message indicating that the message bridge is running:

```
...
2025-07-08 13:32:40 [agntcy_app_sdk.bridge] [INFO] [loop_forever:57] Message bridge is running. Waiting for messages...
```

Next, we can run the weather client:

```bash
uv run python weather_client.py
```

You should see a print output with the weather report:

```
root=SendMessageSuccessResponse(id='1c24a07e-45af-4800-81bc-cc2fd1b579e1', jsonrpc='2.0', result=Message(contextId=None, kind='message', messageId='e68913c7-312d-4bfe-88f6-4b4179d4b5bd', metadata=None, parts=[Part(root=TextPart(kind='text', metadata=None, text='The weather is sunny with a high of 75°F.'))], referenceTaskIds=None, role=<Role.agent: 'agent'>, taskId=None))
```

🚀 Congratulations! You have successfully created two A2A agents that communicate over a SLIM transport.

For a fully functional multi-agent example integrating A2A, Agntcy, and Langgraph, check out our [coffeeAgntcy](https://github.com/agntcy/coffeeAgntcy).

### ⚙️ Contributing additional Transports

To contribute a new transport implementation, follow these steps:

1. **Implement the Transport Interface**: Create a new class for your transport in the `src/agntcy_app_sdk/transports` directory. Ensure it inherits from the `BaseTransport` interface and implements all required methods.

2. **Update the Factory**: Modify the `AgntcyFactory` to include your new transport in the `create_transport` method.

3. **Add Tests**: Create unit tests for your transport in the `tests/e2e` directory. Ensure all tests pass.

4. **Documentation**: Update the documentation to include your new transport. This includes any relevant sections in the README and API reference.

5. **Submit a Pull Request**: Once your changes are complete, submit a pull request for review.

See [API Reference](API_REFERENCE.md) for detailed SDK API documentation.
