# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
import os
from typing import Optional

import uvicorn
from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler

logger = get_logger(__name__)

# Environment variable and default for the JSONRPC/HTTP bind interface.
_HTTP_BIND_HOST_ENV = "AGNTCY_A2A_HTTP_BIND_HOST"
_DEFAULT_HTTP_BIND_HOST = "0.0.0.0"


def resolve_http_bind_host(explicit: Optional[str] = None) -> str:
    """Resolve the interface a JSONRPC/HTTP server should bind to.

    This is independent of the advertised card URL host. Precedence:

    1. ``explicit`` value (e.g. from ``ContainerBuilder.with_bind_host()`` /
       ``CardBuilder.with_http_bind_host()``),
    2. the ``AGNTCY_A2A_HTTP_BIND_HOST`` environment variable,
    3. ``0.0.0.0`` (bind all interfaces).

    Use ``127.0.0.1`` to restrict binding to loopback.
    """
    return explicit or os.environ.get(_HTTP_BIND_HOST_ENV) or _DEFAULT_HTTP_BIND_HOST


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
        bind_host: Optional[str] = None,
    ):
        # BaseA2AServerHandler -> ServerHandler expects (managed_object, ...)
        super().__init__(server, transport=None, topic=None)
        self._server = server
        self._host = host
        self._bind_host = bind_host or host
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
        1. Stamp ``preferred_transport`` on the agent card (if not already set).
        2. Build the ASGI application from the ``A2AStarletteApplication``.
        3. Create a ``uvicorn.Server`` and launch it as a background task.
        4. Push to directory if available.
        """
        # Only stamp preferred_transport when it is unset.  When
        # CardBuilder registers multiple handlers on the same card, the
        # user-declared preferredTransport must not be overwritten.
        current = self.agent_card.preferred_transport
        if current is None:
            self._set_preferred_transport("JSONRPC")

        app = self._server.build()
        config = uvicorn.Config(
            app=app,
            host=self._bind_host,
            port=self._port,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(config)

        # --- Serve in background ---
        self._server_task = asyncio.create_task(
            self._uvicorn_server.serve(),
            name="jsonrpc-server",
        )
        logger.debug(
            f"JSONRPC A2A handler bound {self._bind_host}:{self._port} "
            f"(advertised host={self._host})"
        )

    async def teardown(self) -> None:
        """Stop the Uvicorn server."""
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True

        if self._server_task is not None and not self._server_task.done():
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            logger.debug("JSONRPC server task finished.")
