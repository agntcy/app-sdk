# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from typing import Optional

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler

logger = get_logger(__name__)


class A2AJsonRpcServerHandler(BaseA2AServerHandler):
    """A2A handler that serves the application over native HTTP JSONRPC.

    Unlike ``A2AExperimentalServerHandler``, this handler does **not** use a
    ``BaseTransport``.  Instead it builds the ASGI application from the
    ``A2AStarletteApplication`` and runs it with Uvicorn as a background
    task.

    This is the default path when a user calls
    ``session.add(server).build()`` with an ``A2AStarletteApplication``
    but does **not** call ``.with_transport()``.

    Construction::

        handler = A2AJsonRpcServerHandler(server, host="0.0.0.0", port=9999)
    """

    def __init__(
        self,
        server: A2AStarletteApplication,
        *,
        host: str,
        port: int,
        directory: Optional[BaseAgentDirectory] = None,
    ):
        # BaseA2AServerHandler -> ServerHandler expects (managed_object, ...)
        super().__init__(server, transport=None, topic=None, directory=directory)
        self._server = server
        self._host = host
        self._port = port
        self._server_task: Optional[asyncio.Task] = None
        self._uvicorn_server: Optional[uvicorn.Server] = None

    # -- agent_card property (required by BaseA2AServerHandler) -----------

    @property
    def agent_card(self) -> AgentCard:
        return self._server.agent_card

    # -- Lifecycle --------------------------------------------------------

    async def setup(self) -> None:
        """Build the ASGI app and start Uvicorn in the background.

        Steps:
        1. Stamp ``preferred_transport`` on the agent card.
        2. Build the ASGI application from the ``A2AStarletteApplication``.
        3. Create a ``uvicorn.Server`` and launch it as a background task.
        4. Push to directory if available.
        """
        self._set_preferred_transport("JSONRPC")

        app = self._server.build()
        config = uvicorn.Config(
            app=app,
            host=self._host,
            port=self._port,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(config)

        # --- Directory push ---
        if self._directory:
            await self._directory.setup()
            await self._directory.push_agent_record(self._server.agent_card)

        # --- Serve in background ---
        self._server_task = asyncio.create_task(
            self._uvicorn_server.serve(),
            name="jsonrpc-server",
        )
        logger.info(f"JSONRPC A2A handler started on {self._host}:{self._port}")

    async def teardown(self) -> None:
        """Stop the Uvicorn server."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

        if self._server_task is not None and not self._server_task.done():
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            logger.info("JSONRPC server task finished.")
