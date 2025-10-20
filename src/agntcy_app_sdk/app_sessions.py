# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from threading import Lock
from typing import Any

from a2a.server.apps import A2AStarletteApplication
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.base import BaseAgentProtocol
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol

logger = get_logger(__name__)


class AppContainer:
    """
    Container for holding app session components.
    """

    def __init__(
        self,
        server,
        transport: BaseTransport = None,
        directory: BaseAgentDirectory = None,
        topic: str = None,
        host: str = None,
        port: int = None,
    ):
        self.server = server
        self.transport = transport
        self.directory = directory
        self.topic = topic
        self.host = host
        self.port = port
        self.protocol_handler: BaseAgentProtocol = self._register_protocol_handler(
            server
        )
        self.is_running = False

    def _register_protocol_handler(self, server: Any):
        """
        Create and bind the appropriate protocol handler based on the server type.
        """
        from agntcy_app_sdk.factory import AgntcyFactory

        factory = AgntcyFactory()

        if isinstance(server, A2AStarletteApplication):
            if self.topic is None or self.topic == "":
                self.topic = A2AProtocol.create_agent_topic(server.agent_card)
            handler = factory.create_protocol("A2A")
            handler.bind_server(server)
            return handler
        elif isinstance(server, MCPServer):
            if self.topic is None or self.topic == "":
                raise ValueError("Topic must be provided for MCP server")
            logger.info(f"Creating MCP bridge for topic: {self.topic}")
            handler = factory.create_protocol("MCP")
            handler.bind_server(server)
            return handler
        elif isinstance(server, FastMCP):
            if self.topic is None or self.topic == "":
                raise ValueError("Topic must be provided for FastMCP server")
            logger.info(f"Creating FastMCP bridge for topic: {self.topic}")
            handler = factory.create_protocol("FastMCP")
            handler.bind_server(server)
            return handler
        else:
            raise ValueError("Unsupported server type")

    def set_transport(self, transport: BaseTransport):
        self.transport = transport

    def set_directory(self, directory: BaseAgentDirectory):
        self.directory = directory

    def set_topic(self, topic: str):
        self.topic = topic

    async def run(
        self, blocking: bool = False, push_to_directory_on_startup: bool = False
    ):
        """Start all components of the app container."""
        if self.is_running:
            logger.warning("App session is already running.")
            return

        if self.transport is None:
            raise ValueError("Transport must be set before running.")
        if self.protocol_handler is None:
            raise ValueError("Protocol handler must be set before running.")
        if self.topic is None:
            raise ValueError("Topic must be set before running.")

        # call the transport setup method, any async logic should be handled there
        await self.transport.setup()

        # call the directory setup method, any async logic should be handled there
        if self.directory:
            await self.directory.setup()

        # set callback to process incoming messages
        self.transport.set_callback(self.protocol_handler.handle_message)

        # Subscribe to the specified topic
        await self.transport.subscribe(self.topic)

        if push_to_directory_on_startup and self.directory:
            await self.directory.push_agent_record(self.protocol_handler.agent_record())

        # call the protocol handler setup method, any async logic should be handled there
        await self.protocol_handler.setup()

        logger.info("App session started.")
        self.is_running = True

        if blocking:
            # Run the loop forever if blocking is True
            await self.loop_forever()

    async def loop_forever(self):
        """Keep the event loop running."""
        while True:
            await asyncio.sleep(0.1)

    async def stop(self):
        """Stop all components of the app container."""
        if self.transport:
            await self.transport.close()
        # TODO: add any protocol handler or directory cleanup if needed
        logger.info("App session stopped.")
        self.is_running = False


class AppSession:
    """
    Manages the application session, including transport, protocol handler, and directory.
    """

    def __init__(
        self,
        max_sessions: int = 10,
    ):
        self.max_sessions = max_sessions
        self.app_containers = {}
        self._lock = Lock()

    def add_app_container(self, session_id: str, container: AppContainer):
        if len(self.app_containers) >= self.max_sessions:
            raise RuntimeError("Maximum number of app sessions reached.")
        self.app_containers[session_id] = container

    def get_app_container(self, session_id: str) -> AppContainer:
        return self.app_containers.get(session_id)

    def remove_app_container(self, session_id: str):
        if session_id in self.app_containers:
            if self.app_containers[session_id].is_running:
                raise RuntimeError("Cannot remove a running session. Stop it first.")
            del self.app_containers[session_id]

    async def start_session(
        self,
        session_id: str,
        blocking: bool = False,
        push_to_directory_on_startup: bool = False,
    ):
        """Start a specific app container."""
        container = self.get_app_container(session_id)
        if not container:
            raise ValueError(f"No app container found for session ID: {session_id}")
        if not container.is_running:
            await container.run(
                blocking=blocking,
                push_to_directory_on_startup=push_to_directory_on_startup,
            )

    async def stop_session(self, session_id: str):
        """Stop a specific app container."""
        container = self.get_app_container(session_id)
        if not container:
            raise ValueError(f"No app container found for session ID: {session_id}")
        if container.is_running:
            await container.stop()

    async def start_all_sessions(
        self, blocking: bool = False, push_to_directory_on_startup: bool = False
    ):
        """Start all app containers."""
        for container in self.app_containers.values():
            if not container.is_running:
                await container.run(
                    blocking=blocking,
                    push_to_directory_on_startup=push_to_directory_on_startup,
                )

    async def stop_all_sessions(self):
        """Stop all running app containers."""
        for container in self.app_containers.values():
            if container.is_running:
                await container.stop()
