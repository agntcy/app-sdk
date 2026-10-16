# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""
Docker Monitoring MCP Server

Exposes Docker container monitoring tools via MCP over
an Agntcy SDK transport (SLIM or NATS).

Topic: docker_monitor.mcp

Requires: pip install docker
Requires: Docker daemon running
"""

import asyncio
import argparse
import json
from typing import Any

import docker
from docker.models.containers import Container
from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.factory import AgntcyFactory

factory = AgntcyFactory(enable_tracing=False)


def _get_docker_client() -> docker.DockerClient:
    """Create a Docker client from the local environment."""
    return docker.from_env()


def _image_name(container: Container) -> str:
    """First tag of the container's image, or its truncated id."""
    image = container.image
    if image is None:
        raise ValueError(f"Container {container.name} has no image")
    if image.tags:
        return str(image.tags[0])
    if image.id is None:
        raise ValueError(f"Image of container {container.name} has no id")
    return str(image.id[:12])


def _stats(container: Container) -> dict[str, Any]:
    """One stats snapshot for *container*."""
    stats = container.stats(stream=False)
    if not isinstance(stats, dict):
        raise TypeError(f"Expected a stats dict, got {type(stats).__name__}")
    return stats


async def main(
    transport_type: str, endpoint: str, name: str, block: bool = True
) -> None:
    mcp = FastMCP()

    @mcp.tool()
    async def list_containers() -> str:
        """List all running Docker containers with name, image, and status."""
        client = _get_docker_client()
        containers = client.containers.list()
        result = []
        for c in containers:
            result.append(
                {
                    "id": c.short_id,
                    "name": c.name,
                    "image": _image_name(c),
                    "status": c.status,
                }
            )
        client.close()
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_container_stats(container_name_or_id: str) -> str:
        """Get CPU and memory usage for a specific container.

        Args:
            container_name_or_id: The container name or ID to inspect.
        """
        client = _get_docker_client()
        container = client.containers.get(container_name_or_id)
        stats = _stats(container)

        cpu_delta = (
            stats["cpu_stats"]["cpu_usage"]["total_usage"]
            - stats["precpu_stats"]["cpu_usage"]["total_usage"]
        )
        system_delta = (
            stats["cpu_stats"]["system_cpu_usage"]
            - stats["precpu_stats"]["system_cpu_usage"]
        )
        num_cpus = stats["cpu_stats"].get("online_cpus", 1)
        cpu_percent = (
            (cpu_delta / system_delta) * num_cpus * 100.0 if system_delta > 0 else 0.0
        )

        mem_usage = stats["memory_stats"].get("usage", 0)
        mem_limit = stats["memory_stats"].get("limit", 1)
        mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0

        result = {
            "container": container.name,
            "cpu_percent": round(cpu_percent, 2),
            "memory_usage_mb": round(mem_usage / (1024**2), 2),
            "memory_limit_mb": round(mem_limit / (1024**2), 2),
            "memory_percent": round(mem_percent, 2),
        }
        client.close()
        return json.dumps(result, indent=2)

    @mcp.tool()
    async def get_all_container_stats() -> str:
        """Get CPU and memory overview for all running containers."""
        client = _get_docker_client()
        containers = client.containers.list()
        results = []

        for container in containers:
            stats = _stats(container)

            cpu_delta = (
                stats["cpu_stats"]["cpu_usage"]["total_usage"]
                - stats["precpu_stats"]["cpu_usage"]["total_usage"]
            )
            system_delta = (
                stats["cpu_stats"]["system_cpu_usage"]
                - stats["precpu_stats"]["system_cpu_usage"]
            )
            num_cpus = stats["cpu_stats"].get("online_cpus", 1)
            cpu_percent = (
                (cpu_delta / system_delta) * num_cpus * 100.0
                if system_delta > 0
                else 0.0
            )

            mem_usage = stats["memory_stats"].get("usage", 0)
            mem_limit = stats["memory_stats"].get("limit", 1)
            mem_percent = (mem_usage / mem_limit) * 100.0 if mem_limit > 0 else 0.0

            results.append(
                {
                    "container": container.name,
                    "cpu_percent": round(cpu_percent, 2),
                    "memory_usage_mb": round(mem_usage / (1024**2), 2),
                    "memory_percent": round(mem_percent, 2),
                }
            )

        client.close()
        return json.dumps(results, indent=2)

    transport = factory.create_transport(transport_type, endpoint=endpoint, name=name)

    app_session = factory.create_app_session(max_sessions=1)
    app_session.add(mcp._mcp_server).with_transport(transport).with_topic(
        "docker_monitor.mcp"
    ).with_session_id("default_session").build()
    await app_session.start_all_sessions(keep_alive=block)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run the Docker Monitor MCP server with a specified transport."
    )
    parser.add_argument(
        "--transport",
        type=str,
        choices=AgntcyFactory().registered_transports(),
        default="NATS",
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
        default="default/default/docker_monitor.mcp",
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
