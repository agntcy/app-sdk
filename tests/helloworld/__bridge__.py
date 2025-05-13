import click

from agent_executor import HelloWorldAgentExecutor

from a2a.server import A2AServer
from a2a.server.request_handlers import DefaultA2ARequestHandler
from a2a.types import (
    AgentAuthentication,
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)
import asyncio
from gateway_sdk.factory import GatewayFactory
from gateway_sdk.nats.gateway import NatsGateway

async def main():
    skill = AgentSkill(
        id='hello_world',
        name='Returns hello world',
        description='just returns hello world',
        tags=['hello world'],
        examples=['hi', 'hello world'],
    )

    agent_card = AgentCard(
        name='Hello World Agent',
        description='Just a hello world agent',
        url='http://localhost:9999/',
        version='1.0.0',
        defaultInputModes=['text'],
        defaultOutputModes=['text'],
        capabilities=AgentCapabilities(),
        skills=[skill],
        authentication=AgentAuthentication(schemes=['public']),
    )

    request_handler = DefaultA2ARequestHandler(
        agent_executor=HelloWorldAgentExecutor()
    )

    server = A2AServer(agent_card=agent_card, request_handler=request_handler)

    factory = GatewayFactory()
    transport = NatsGateway(endpoint='localhost:4222')
    bridge = factory.create_bridge(server, transport=transport)

    await bridge.start()

    try:
        # Keep the bridge running
        print("Bridge is running. Press Ctrl+C to exit.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")

    #await server.start(host="0.0.0.0", port=9999)


if __name__ == '__main__':
    print('Running as main')
    asyncio.run(main())
