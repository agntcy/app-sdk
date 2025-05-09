# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from simple_agentic_app.simple_agentic_app import simple_autogen_app

import argparse
from gateway_sdk.factory import GatewayFactory

async def run_agent(address):
    agent = simple_autogen_app()

    local_org = "cisco"
    local_ns = "default"
    local_agent = "autogen"

    factory = GatewayFactory()
    gateway = factory.create_gateway("AGP", endpoint=address)
    
    async def callback(message):
        # handle received messages
        result = await agent.initate_chat(message)

        # process response
        result.inner_messages
        weather_question = result.inner_messages[-1].content[-1].content.split(":")
        if weather_question[0] == "WEATHER":
            return weather_question[1]

    await gateway.subscribe(local_org, local_ns, local_agent, callback)

async def main():
    parser = argparse.ArgumentParser(description="Command line client for message passing.")
    parser.add_argument("-g", "--gateway", type=str, help="Gateway address.", default="http://localhost:46357")
    args = parser.parse_args()
    await run_agent(args.gateway)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated by user.")