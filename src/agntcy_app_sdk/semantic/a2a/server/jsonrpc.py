# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from typing import Optional

import uvicorn
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import find_interface
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.semantic.a2a.server.config import A2AServerConfig

logger = get_logger(__name__)


class A2AJsonRpcServerHandler(BaseA2AServerHandler):
    """A2A handler that serves the application over native HTTP JSONRPC.

    Unlike ``A2AExperimentalServerHandler``, this handler does **not** use a
    ``BaseTransport``.  Instead it builds a Starlette application from the
    a2a-sdk route factories (via :meth:`A2AServerConfig.build_app`) and runs
    it with Uvicorn as a background task.

    This is the default path when a user calls
    ``session.add(server).build()`` with an :class:`A2AServerConfig`
    but does **not** call ``.with_transport()``.

    The card must tell clients where the endpoint is reachable, via a
    ``JSONRPC`` entry in ``supported_interfaces``.  The bind address
    (``host`` / ``port``) is *not* used for that: it is often ``0.0.0.0`` or
    an in-container address.  Instead:

    * an interface the author already declared is left untouched;
    * otherwise, if ``public_url`` is given, a ``JSONRPC`` interface with
      that URL is **appended** (never prepended, so the author's preference
      order and any patterns transport stay preferred);
    * otherwise a warning is logged and the card is left as is.

    Construction::

        handler = A2AJsonRpcServerHandler(
            server,
            host="0.0.0.0",
            port=9999,
            public_url="https://agent.example.com",
        )
    """

    def __init__(
        self,
        server: A2AServerConfig,
        *,
        host: str,
        port: int,
        public_url: Optional[str] = None,
    ):
        # BaseA2AServerHandler -> ServerHandler expects (managed_object, ...)
        super().__init__(server, transport=None, topic=None)
        self._server = server
        self._host = host
        self._port = port
        self._public_url = public_url
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
        1. Declare the JSONRPC interface from ``public_url`` if the card has
           none (otherwise warn).
        2. Build the ASGI application from the a2a-sdk route factories.
        3. Create a ``uvicorn.Server`` and launch it as a background task.
        4. Push to directory if available.
        """
        self._declare_jsonrpc_interface()

        app = self._server.build_app()
        config = uvicorn.Config(
            app=app,
            host=self._host,
            port=self._port,
            loop="asyncio",
        )
        self._uvicorn_server = uvicorn.Server(config)

        # --- Serve in background ---
        self._server_task = asyncio.create_task(
            self._uvicorn_server.serve(),
            name="jsonrpc-server",
        )
        logger.debug(f"JSONRPC A2A handler started on {self._host}:{self._port}")

    def _declare_jsonrpc_interface(self) -> None:
        """Make sure the card advertises where JSON-RPC is reachable."""
        if find_interface(self.agent_card, "JSONRPC") is not None:
            # Author-declared: keep URL and position exactly as written.
            return

        if self._public_url:
            self._declare_interface("JSONRPC", self._public_url, prefer=False)
            return

        logger.warning(
            "Agent card has no JSONRPC entry in supported_interfaces, so "
            "clients cannot discover the HTTP endpoint served on "
            f"{self._host}:{self._port}. Declare it on the card, e.g. "
            "AgentInterface(protocol_binding='JSONRPC', "
            "url='https://<public-host>'), or call .with_public_url(...) "
            "on the builder."
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
