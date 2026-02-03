# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Host Monitoring MCP Server

Exposes system monitoring tools (CPU, memory, uptime) via MCP over
an Agntcy SDK transport (SLIM or NATS).

Topic: host_monitor.mcp

Requires: pip install psutil
"""

import asyncio
import argparse
import json
import time

import psutil
from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.factory import AgntcyFactory, TransportTypes
from agntcy_app_sdk.app_sessions import AppContainer

factory = AgntcyFactory(enable_tracing=False)


async def main(transport_type: str, endpoint: str, name: str, block: bool = True):
    mcp = FastMCP()

    @mcp.tool()
    async def get_cpu_usage() -> str:
        """Return per-core and overall CPU utilization percentages."""
        per_core = psutil.cpu_percent(interval=1, percpu=True)
        overall = psutil.cpu_percent(interval=0)
        result = {
            "overall_percent": overall,
            "per_core_percent": per_core,
            "core_count": psutil.cpu_count(logical=True),
        }
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_memory_usage() -> str:
        """Return RAM statistics: total, available, used, and percent used."""
        mem = psutil.virtual_memory()
        result = {
            "total_bytes": mem.total,
            "available_bytes": mem.available,
            "used_bytes": mem.used,
            "percent_used": mem.percent,
            "total_gb": round(mem.total / (1024**3), 2),
            "available_gb": round(mem.available / (1024**3), 2),
            "used_gb": round(mem.used / (1024**3), 2),
        }
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_system_summary() -> str:
        """Return a combined summary of CPU, memory, and system uptime."""
        cpu_overall = psutil.cpu_percent(interval=1)
        cpu_per_core = psutil.cpu_percent(interval=0, percpu=True)
        mem = psutil.virtual_memory()
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        hours, remainder = divmod(int(uptime_seconds), 3600)
        minutes, seconds = divmod(remainder, 60)

        result = {
            "cpu": {
                "overall_percent": cpu_overall,
                "per_core_percent": cpu_per_core,
                "core_count": psutil.cpu_count(logical=True),
            },
            "memory": {
                "total_gb": round(mem.total / (1024**3), 2),
                "used_gb": round(mem.used / (1024**3), 2),
                "available_gb": round(mem.available / (1024**3), 2),
                "percent_used": mem.percent,
            },
            "uptime": f"{hours}h {minutes}m {seconds}s",
        }
        return json.dumps(result, indent=2)

    transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)

    app_session = factory.create_app_session(max_sessions=1)
    app_container = AppContainer(
        mcp._mcp_server,
        transport=transport,
        topic="host_monitor.mcp",
    )
    app_session.add_app_container("default_session", app_container)
    await app_session.start_all_sessions(keep_alive=block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the Host Monitor MCP server with a specified transport."
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
        "--name",
        type=str,
        default="default/default/host_monitor.mcp",
        help="Name of the server instance",
    )
    parser.add_argument(
        "--non-blocking",
        action="store_false",
        dest="block",
        help="Run the server in non-blocking mode (default: blocking)",
    )

    args = parser.parse_args()
    asyncio.run(main(args.transport, args.endpoint, args.name, args.block))
