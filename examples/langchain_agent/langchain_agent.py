# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio

'''from simple_weather_agent.simple_weather_agent import (
    SIMPLE_WEATHER_AGENT_WITH_TOOLS,
)'''

import argparse
from gateway_sdk.factory import GatewayFactory


async def run_agent(message, address):
    #agent = SIMPLE_WEATHER_AGENT_WITH_TOOLS()

    remote_org = "cisco"
    remote_ns = "default"
    remote_agent = "autogen"

    factory = GatewayFactory()
    gateway = factory.create_gateway("AGP", endpoint=address)
    
    response = await gateway.request(remote_org, remote_ns, remote_agent, message.encode())
    print(f"Received response: {response}")


async def main():
    parser = argparse.ArgumentParser(description="Command line client for message passing.")
    parser.add_argument("-m", "--message", type=str, help="Message to send.")
    parser.add_argument("-g", "--gateway", type=str, help="Gateway address.", default="http://localhost:46357")
    args = parser.parse_args()

    await run_agent(args.message, args.gateway)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated by user.")