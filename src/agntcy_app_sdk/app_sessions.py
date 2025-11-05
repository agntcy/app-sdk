# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from typing import Any, Optional
import signal

from a2a.server.apps import A2AStarletteApplication
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.fastmcp import FastMCP

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.semantic.base import BaseAgentProtocol
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.protocol import A2AProtocol
from agntcy_app_sdk.semantic.translator import SemanticTranslator

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
        self.translator = SemanticTranslator()
        self._shutdown_event: Optional[asyncio.Event] = None
        self.is_running = False

    def set_transport(self, transport: BaseTransport):
        self.transport = transport

    def set_directory(self, directory: BaseAgentDirectory):
        self.directory = directory

    def set_topic(self, topic: str):
        self.topic = topic

    async def run(
        self, keep_alive: bool = False, push_to_directory_on_startup: bool = False
    ):
        """
        Start all components of the app container.
        
        STARTUP ORDER:
        1. Transport      - Network layer (must be first)
        2. Directory      - Service registry
        3. Identity       - Authentication (needs network)
        4. Protocol       - Message handler (needs identity)
        5. Subscription   - Topic subscription (needs protocol)
        
        SHUTDOWN ORDER:
        5. Subscription   - Unsubscribe (first)
        4. Protocol       - Stop handling
        3. Identity       - Cleanup sessions
        2. Directory      - Deregister
        1. Transport      - Close connections (last)

        Args:
            keep_alive (bool): Whether to keep the session alive.
            push_to_directory_on_startup (bool): Whether to push to directory on startup.
        """
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

        if keep_alive:
            # Run the loop forever if keep_alive is True
            await self.loop_forever()

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

    async def loop_forever(self):
        """Keep the event loop running until shutdown signal received."""
        self._shutdown_event = asyncio.Event()
        self.is_running = True

        loop = asyncio.get_running_loop()

        # Register signal handlers for graceful shutdown
        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig, lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
                )
                logger.debug(f"Registered handler for {sig.name}")
        except NotImplementedError:
            # Happens on Windows or inside restricted loops
            logger.warning("Signal handlers not supported in this environment.")

        logger.info("App started. Waiting for shutdown signal (Ctrl+C)...")

        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            logger.info("Event loop cancelled; shutting down gracefully...")
        finally:
            await self.stop()

    async def _handle_shutdown(self, sig: signal.Signals):
        """Handle shutdown signals gracefully."""
        if not self._shutdown_event.is_set():
            logger.warning(f"Received signal {sig.name}, initiating shutdown...")
            self._shutdown_event.set()
        else:
            logger.debug(f"Ignoring duplicate signal: {sig.name}")

    async def stop(self):
        """Stop all components of the app container."""
        logger.info("Stopping app session...")

        if self.transport:
            try:
                await self.transport.close()
                logger.info("Transport closed cleanly.")
            except Exception as e:
                logger.exception(f"Error closing transport: {e}")

        self.is_running = False
        logger.info("App session stopped. Exiting event loop.")


class AppSession:
    """
    Manages the agent application session, including transport, protocol handler, and directory.
    """

    def __init__(
        self,
        max_sessions: int = 10,
    ):
        self.max_sessions = max_sessions
        self.app_containers = {}
        self._lock = asyncio.Lock()

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
        keep_alive: bool = False,
        push_to_directory_on_startup: bool = False,
        **kwargs,
    ):
        """
        Start a specific app container.

        Args:
            session_id (str): The ID of the session to start.
            keep_alive (bool): Whether to keep the session alive.
            push_to_directory_on_startup (bool): Whether to push to directory on startup.
        """
        container = self.get_app_container(session_id)
        if not container:
            raise ValueError(f"No app container found for session ID: {session_id}")
        if not container.is_running:
            await container.run(
                keep_alive=keep_alive,
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
        self, keep_alive: bool = False, push_to_directory_on_startup: bool = False
    ):
        """Start all app containers."""
        for container in self.app_containers.values():
            if not container.is_running:
                await container.run(
                    keep_alive=keep_alive,
                    push_to_directory_on_startup=push_to_directory_on_startup,
                )

    async def stop_all_sessions(self):
        """Stop all running app containers."""
        for container in self.app_containers.values():
            if container.is_running:
                await container.stop()
