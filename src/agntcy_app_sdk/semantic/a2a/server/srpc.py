# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

import slim_bindings

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.transport.slim.common import (
    get_or_create_slim_instance,
    shared_secret_identity,
    split_id,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class A2ASRPCConfig:
    """Configuration object for the slimrpc-based A2A handler.

    Users pass an instance of this to ``session.add(config)`` instead of
    an ``A2AStarletteApplication``.  The SDK will internally create the
    ``SRPCHandler`` and ``slim_bindings.Server``.

    Required fields:
        agent_card: The A2A AgentCard describing this agent.
        request_handler: A ``DefaultRequestHandler`` (from ``a2a-sdk``)
            that implements the agent's business logic.
        slimrpc_server_config: Dict with keys ``identity``,
            ``slim_client_config``, and ``shared_secret``.

    Optional fields:
        context_builder: Optional callable to build per-request context.
        card_modifier: Optional callable to modify the agent card before
            it is published (e.g. add security schemes).
    """

    agent_card: AgentCard
    request_handler: DefaultRequestHandler
    slimrpc_server_config: dict[str, Any]
    context_builder: Optional[Callable[..., Any]] = None
    card_modifier: Optional[Callable[[AgentCard], AgentCard]] = None

    def __post_init__(self) -> None:
        required_keys = {"identity", "slim_client_config", "shared_secret"}
        missing = required_keys - set(self.slimrpc_server_config.keys())
        if missing:
            raise ValueError(
                f"slimrpc_server_config is missing required keys: {missing}"
            )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class A2ASRPCServerHandler(BaseA2AServerHandler):
    """A2A handler that uses native SLIM RPC (slimrpc).

    Unlike ``A2APatternsServerHandler``, this handler does **not** use a
    ``BaseTransport``.  Instead it creates a ``slim_bindings.Server``
    internally, registers the A2A servicer, and runs it as a background
    task.

    Construction::

        handler = A2ASRPCServerHandler(config, directory=my_dir)

    Where *config* is an :class:`A2ASRPCConfig` instance.
    """

    def __init__(
        self,
        config: A2ASRPCConfig,
        *,
        directory: Optional[BaseAgentDirectory] = None,
    ):
        # BaseA2AServerHandler → ServerHandler expects (managed_object, ...)
        # We pass the config — each handler knows what its managed object is.
        super().__init__(config, transport=None, topic=None, directory=directory)
        self._config = config
        self._server_task: Optional[asyncio.Task] = None
        self._slim_rpc_server: Optional[slim_bindings.Server] = None

    # -- agent_card property (required by BaseA2AServerHandler) -----------

    @property
    def agent_card(self) -> AgentCard:
        return self._config.agent_card

    # -- Lifecycle --------------------------------------------------------

    async def setup(self) -> None:
        """Create the slimrpc server and start serving in the background.

        Steps:
        1. Stamp ``preferred_transport`` on the agent card.
        2. Create a SLIM ``App`` + ``Server`` from the user-provided config.
        3. Import ``slima2a`` and register the A2A servicer.
        4. Launch ``server.serve_async()`` as a background task.
        5. Push to directory if available.
        """
        self._set_preferred_transport("slimrpc")

        # --- Apply optional card modifier ---
        if self._config.card_modifier is not None:
            self._config.agent_card = self._config.card_modifier(
                self._config.agent_card
            )

        # --- Build the SLIM App ---
        srpc_cfg = self._config.slimrpc_server_config
        identity_str: str = srpc_cfg["identity"]
        slim_client_config: dict = srpc_cfg["slim_client_config"]
        shared_secret: str = srpc_cfg["shared_secret"]

        name = split_id(identity_str)
        provider, verifier = shared_secret_identity(
            identity=identity_str,
            secret=shared_secret,
        )

        endpoint = slim_client_config.get("endpoint", "http://localhost:46357")
        tls_cfg = slim_client_config.get("tls", {})
        tls_insecure = tls_cfg.get("insecure", True)

        # Reuse the existing helper that initialises the global SLIM runtime
        _service, slim_app, connection_id = await get_or_create_slim_instance(
            local=name,
            slim_endpoint=endpoint,
            slim_insecure_client=tls_insecure,
            shared_secret=shared_secret,
        )

        # --- Create the RPC server ---
        self._slim_rpc_server = slim_bindings.Server.new_with_connection(
            slim_app, name, connection_id
        )

        # --- Register the A2A servicer (from slima2a) ---
        try:
            from slima2a.server import SRPCHandler
            from slima2a.gen.a2a_grpc import add_A2AServiceServicer_to_server
        except ImportError as exc:
            raise ImportError(
                "The 'slima2a' package is required for A2ASRPCServerHandler. "
                "Install it with: pip install slim-a2a-python"
            ) from exc

        srpc_handler_kwargs = {
            "agent_card": self._config.agent_card,
            "request_handler": self._config.request_handler,
        }
        if self._config.context_builder is not None:
            srpc_handler_kwargs["context_builder"] = self._config.context_builder

        servicer = SRPCHandler(**srpc_handler_kwargs)
        add_A2AServiceServicer_to_server(servicer, self._slim_rpc_server)

        # --- Directory push ---
        if self._directory:
            await self._directory.setup()
            # Build a record compatible with the directory interface
            await self._directory.push_agent_record(self._config.agent_card)

        # --- Serve in background ---
        self._server_task = asyncio.create_task(
            self._slim_rpc_server.serve_async(),
            name="slimrpc-server",
        )
        logger.info(
            f"slimrpc A2A handler started for identity '{identity_str}'"
        )

    async def teardown(self) -> None:
        """Stop the slimrpc server."""
        if self._slim_rpc_server is not None:
            try:
                await self._slim_rpc_server.shutdown_async()
                logger.info("slimrpc server shut down gracefully.")
            except Exception as e:
                logger.exception(f"Error shutting down slimrpc server: {e}")

        if self._server_task is not None and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            logger.info("slimrpc server task cancelled.")
