# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import signal
from typing import Any, Optional

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.base import ServerHandler
from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Handler auto-detection registry (lazy-loaded)
# ---------------------------------------------------------------------------
_HANDLER_MAP: Optional[dict[type, type]] = None


def _get_handler_map() -> dict[type, type]:
    """Lazily build server-type → handler-class mapping."""
    global _HANDLER_MAP
    if _HANDLER_MAP is None:
        from a2a.server.apps import A2AStarletteApplication
        from mcp.server.fastmcp import FastMCP
        from mcp.server.lowlevel import Server as MCPServer

        from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
            A2AExperimentalServerHandler,
        )
        from agntcy_app_sdk.semantic.a2a.server.srpc import (
            A2ASRPCConfig,
            A2ASRPCServerHandler,
        )
        from agntcy_app_sdk.semantic.fast_mcp.handler import FastMCPServerHandler
        from agntcy_app_sdk.semantic.mcp.handler import MCPServerHandler

        _HANDLER_MAP = {
            A2AStarletteApplication: A2AExperimentalServerHandler,
            A2ASRPCConfig: A2ASRPCServerHandler,
            MCPServer: MCPServerHandler,
            FastMCP: FastMCPServerHandler,
        }
    return _HANDLER_MAP


def _resolve_handler_class(target: Any) -> type:
    """Return the handler class for the given target instance."""
    for target_type, handler_class in _get_handler_map().items():
        if isinstance(target, target_type):
            return handler_class
    raise ValueError(f"Unsupported target type: {type(target).__name__}")


# ---------------------------------------------------------------------------
# ContainerBuilder — fluent API for constructing AppContainer instances
# ---------------------------------------------------------------------------


class ContainerBuilder:
    """Fluent builder for creating and registering an AppContainer."""

    def __init__(self, session: AppSession, target: Any):
        self._session = session
        self._target = target
        self._transport: Optional[BaseTransport] = None
        self._topic: Optional[str] = None
        self._directory: Optional[BaseAgentDirectory] = None
        self._session_id: Optional[str] = None
        self._host: str = "0.0.0.0"
        self._port: int = 9000

    def with_transport(self, transport: BaseTransport) -> ContainerBuilder:
        self._transport = transport
        return self

    def with_topic(self, topic: str) -> ContainerBuilder:
        self._topic = topic
        return self

    def with_directory(self, directory: BaseAgentDirectory) -> ContainerBuilder:
        self._directory = directory
        return self

    def with_session_id(self, session_id: str) -> ContainerBuilder:
        self._session_id = session_id
        return self

    def with_host(self, host: str) -> ContainerBuilder:
        self._host = host
        return self

    def with_port(self, port: int) -> ContainerBuilder:
        self._port = port
        return self

    def build(self) -> AppContainer:
        """Resolve handler from target type, construct AppContainer, register it."""
        handler_class = _resolve_handler_class(self._target)

        # A2ASRPCServerHandler takes (config) — no transport or topic
        from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASRPCServerHandler

        # When the target is an A2AStarletteApplication but no transport was
        # provided, serve it over native HTTP JSONRPC instead of going through
        # the patterns handler (which requires a transport).
        from agntcy_app_sdk.semantic.a2a.server.jsonrpc import A2AJsonRpcServerHandler
        from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
            A2AExperimentalServerHandler,
        )

        if handler_class is A2AExperimentalServerHandler and self._transport is None:
            handler = A2AJsonRpcServerHandler(
                self._target,
                host=self._host,
                port=self._port,
            )
        elif handler_class is A2ASRPCServerHandler:
            if self._transport is not None or self._topic is not None:
                logger.warning(
                    "transport and topic are ignored for A2ASRPCConfig; "
                    "slimrpc manages its own transport."
                )
            handler = handler_class(
                self._target,
            )
        else:
            handler = handler_class(
                self._target,
                transport=self._transport,
                topic=self._topic,
            )

        container = AppContainer(handler, directory=self._directory)

        if self._session_id is not None:
            self._session._register_container(self._session_id, container)

        return container


# ---------------------------------------------------------------------------
# AppContainer — thin lifecycle wrapper around a ServerHandler
# ---------------------------------------------------------------------------


class AppContainer:
    """Container for holding app session components."""

    def __init__(
        self,
        handler: ServerHandler,
        *,
        directory: Optional[BaseAgentDirectory] = None,
    ):
        self.handler = handler
        self._directory = directory
        self._shutdown_event: Optional[asyncio.Event] = None
        self.is_running = False

    # -- Convenience properties that delegate to the handler ----------------
    @property
    def topic(self) -> Optional[str]:
        return self.handler.topic

    @property
    def transport(self) -> Optional[BaseTransport]:
        return self.handler.transport

    @property
    def directory(self) -> Optional[BaseAgentDirectory]:
        return self._directory

    # -- Lifecycle ----------------------------------------------------------

    async def run(self, keep_alive: bool = False):
        """Start all components of the app container."""
        if self.is_running:
            logger.warning("App session is already running.")
            return

        await self.handler.setup()

        # Directory lifecycle: setup + push record (if configured)
        if self._directory:
            await self._directory.setup()
            record = self.handler.get_agent_record()
            if record is not None:
                await self._directory.push_agent_record(record)

        self.is_running = True

        logger.info("App session started.")

        if keep_alive:
            await self.loop_forever()

    async def loop_forever(self):
        """Keep the event loop running until shutdown signal received."""
        self._shutdown_event = asyncio.Event()
        self.is_running = True

        loop = asyncio.get_running_loop()

        try:
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(
                    sig, lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
                )
                logger.debug(f"Registered handler for {sig.name}")
        except NotImplementedError:
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
        await self.handler.teardown()
        self.is_running = False
        logger.info("App session stopped. Exiting event loop.")


# ---------------------------------------------------------------------------
# AppSession — manages multiple AppContainer instances
# ---------------------------------------------------------------------------


class AppSession:
    """Manages the agent application session, including transport, protocol handler, and directory."""

    def __init__(self, max_sessions: int = 10):
        self.max_sessions = max_sessions
        self.app_containers: dict[str, AppContainer] = {}
        self._lock = asyncio.Lock()

    # -- Fluent entry point -------------------------------------------------

    def add(self, target: Any) -> ContainerBuilder:
        """Begin building an AppContainer for the given target (server or config)."""
        return ContainerBuilder(self, target)

    # -- Internal registration (called by ContainerBuilder.build()) ---------

    def _register_container(self, session_id: str, container: AppContainer):
        if len(self.app_containers) >= self.max_sessions:
            raise RuntimeError("Maximum number of app sessions reached.")
        self.app_containers[session_id] = container

    # -- Legacy-compatible convenience method (deprecated) -------------------

    def add_app_container(self, session_id: str, container: AppContainer):
        """Deprecated — prefer ``session.add(server).with_...().build()``."""
        self._register_container(session_id, container)

    # -- Lookup / removal ---------------------------------------------------

    def get_app_container(self, session_id: str) -> Optional[AppContainer]:
        return self.app_containers.get(session_id)

    def remove_app_container(self, session_id: str):
        if session_id in self.app_containers:
            if self.app_containers[session_id].is_running:
                raise RuntimeError("Cannot remove a running session. Stop it first.")
            del self.app_containers[session_id]

    # -- Start / stop -------------------------------------------------------

    async def start_session(
        self,
        session_id: str,
        keep_alive: bool = False,
        **kwargs,
    ):
        """Start a specific app container."""
        container = self.get_app_container(session_id)
        if not container:
            raise ValueError(f"No app container found for session ID: {session_id}")
        if not container.is_running:
            await container.run(
                keep_alive=keep_alive,
            )

    async def stop_session(self, session_id: str):
        """Stop a specific app container."""
        container = self.get_app_container(session_id)
        if not container:
            raise ValueError(f"No app container found for session ID: {session_id}")
        if container.is_running:
            await container.stop()

    async def start_all_sessions(self, keep_alive: bool = False):
        """Start all app containers."""
        for container in self.app_containers.values():
            if not container.is_running:
                await container.run(
                    keep_alive=keep_alive,
                )

    async def stop_all_sessions(self):
        """Stop all running app containers."""
        for container in self.app_containers.values():
            if container.is_running:
                await container.stop()
