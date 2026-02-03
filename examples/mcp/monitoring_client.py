# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Monitoring MCP Client

Connects to multiple MCP servers via their topics over a single transport
and calls tools on each one. By default it targets:
  - host_monitor.mcp   — host CPU, memory, and uptime
  - docker_monitor.mcp — Docker container listing and stats

This demonstrates how a single client process can reach multiple MCP
servers by targeting different topics.

Requires: host_monitor_server.py and docker_monitor_server.py running
on the same transport.
"""

import asyncio
import argparse
import json

from agntcy_app_sdk.factory import AgntcyFactory, TransportTypes

factory = AgntcyFactory(enable_tracing=False)


def _extract_text(result) -> str:
    """Extract and pretty-print text from a tool call result."""
    content_list = result.content
    if isinstance(content_list, list) and len(content_list) > 0:
        text = content_list[0].text
        try:
            parsed = json.loads(text)
            return json.dumps(parsed, indent=2)
        except (json.JSONDecodeError, TypeError):
            return text
    return "No content returned."


async def _query_server(topic: str, transport_type: str, endpoint: str):
    """Create a client for the given topic, list tools, and call each one."""
    clean_name = topic.replace(".", "_")
    transport = factory.create_transport(
        transport_type,
        endpoint=endpoint,
        name=f"default/default/client_{clean_name}",
    )

    mcp_client = factory.create_client(
        "MCP",
        agent_topic=topic,
        transport=transport,
    )

    async with mcp_client as client:
        try:
            tools = await client.list_tools()
            tool_names = [t.name for t in tools.tools]

            print(f"Tools on [{topic}]:")
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")
            print()

            # Call one representative host tool if available
            if "get_system_summary" in tool_names:
                print("--- get_system_summary ---")
                result = await client.call_tool(
                    name="get_system_summary", arguments={}
                )
                print(_extract_text(result))
                print()

            # Call one representative container tool if available
            if "list_containers" in tool_names:
                print("--- list_containers ---")
                result = await client.call_tool(
                    name="list_containers", arguments={}
                )
                print(_extract_text(result))
                print()

        finally:
            await transport.close()


async def main(transport_type: str, endpoint: str, topics: list[str]):
    for topic in topics:
        print("=" * 60)
        print(f"Querying topic: {topic}")
        print("=" * 60)
        await _query_server(topic, transport_type, endpoint)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the Monitoring MCP client — queries multiple MCP servers via topics."
    )
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
    parser.add_argument(
        "--topics",
        nargs="+",
        default=["host_monitor.mcp", "docker_monitor.mcp"],
        help="Topics to query (default: host_monitor.mcp docker_monitor.mcp)",
    )

    args = parser.parse_args()
    asyncio.run(main(args.transport, args.endpoint, args.topics))
