from agent_executor import (
    HelloWorldAgentExecutor,  # type: ignore[import-untyped]
)

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

import click
import uvicorn


@click.command()
@click.option("--host", "host", default="localhost")
@click.option("--port", "port", default=9999)
def main(host: str, port: int):
    skill = AgentSkill(
        id="hello_world",
        name="Returns hello world",
        description="just returns hello world",
        tags=["hello world"],
        examples=["hi", "hello world"],
    )

    agent_card = AgentCard(
        name="Hello World Agent",
        description="Just a hello world agent",
        url="http://localhost:9999/",
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],  # Only the basic skill for the public card
        supportsAuthenticatedExtendedCard=True,
    )

    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(),
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    uvicorn.run(server.build(), host=host, port=port)


if __name__ == "__main__":
    main()
