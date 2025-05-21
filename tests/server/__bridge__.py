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
import argparse
from gateway_sdk.factory import TransportTypes
from gateway_sdk.factory import GatewayFactory

async def main(transport_type: str, endpoint: str):
    """
    This is a simple example of how to create a bridge between an A2A server and a transport.
    It creates a Hello World agent and sets up the transport to communicate with it.
    """

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

    factory = GatewayFactory(enable_tracing=True)

    # Create a transport object
    transport = factory.create_transport(transport_type, endpoint=endpoint)
    bridge = factory.create_bridge(server, transport=transport)
    await bridge.start()

    """
    Optional if you want A2A default starllet server running as well
    """

    from uvicorn import Config, Server
    config = Config(app=server.app(), host="0.0.0.0", port=9999, loop="asyncio")
    userver = Server(config)

    # Serve the app. This is a coroutine.
    await userver.serve()

    try:
        # Keep the bridge running
        print("Bridge is running. Press Ctrl+C to exit.")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Shutting down...")

if __name__ == '__main__':
    # get transport type from command line argument
    parser = argparse.ArgumentParser(description="Run the A2A server with a specified transport type.")
    parser.add_argument(
        "--transport",
        type=str,
        choices=[t.value for t in TransportTypes],
        default=TransportTypes.NATS.value,
        help="Transport type to use (default: NATS)",
    )
    parser.add_argument(
        "--endpoint",
        type=str,
        default="localhost:4222",
        help="Endpoint for the transport (default: localhost:4222)",
    )

    args = parser.parse_args()

    print(f"Using transport type: {args.transport}")
    
    asyncio.run(main(args.transport, args.endpoint))
