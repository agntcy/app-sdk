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
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.transport.slim.common import get_or_create_slim_instance, split_id

from slima2a.handler import SRPCHandler
from slima2a.types.a2a_pb2_slimrpc import add_A2AServiceServicer_to_server

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class SlimRpcConnectionConfig:
    """SLIM connectivity parameters for a SlimRPC server.

    The ``identity`` must be in ``"org/namespace/app"`` format.
    """

    identity: str
    shared_secret: str
    endpoint: str = "http://localhost:46357"
    tls_insecure: bool = True


@dataclass
class A2ASlimRpcServerConfig:
    """Configuration object for the slimrpc-based A2A handler.

    Users pass an instance of this to ``session.add(config)`` instead of
    an ``A2AStarletteApplication``.  The SDK will internally create the
    ``SRPCHandler`` and ``slim_bindings.Server``.

    Required fields:
        agent_card: The A2A AgentCard describing this agent.
        request_handler: A ``DefaultRequestHandler`` (from ``a2a-sdk``)
            that implements the agent's business logic.
        connection: A :class:`SlimRpcConnectionConfig` with SLIM
            connectivity parameters (identity, shared_secret, etc.).

    Optional fields:
        context_builder: Optional callable to build per-request context.
        card_modifier: Optional callable to modify the agent card before
            it is published (e.g. add security schemes).
    """

    agent_card: AgentCard
    request_handler: DefaultRequestHandler
    connection: SlimRpcConnectionConfig
    context_builder: Optional[Callable[..., Any]] = None
    card_modifier: Optional[Callable[[AgentCard], AgentCard]] = None


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------


class A2ASRPCServerHandler(BaseA2AServerHandler):
    """A2A handler that uses native SLIM RPC (slimrpc).

    Unlike ``A2AExperimentalServerHandler``, this handler does **not** use a
    ``BaseTransport``.  Instead it creates a ``slim_bindings.Server``
    internally, registers the A2A servicer, and runs it as a background
    task.

    Construction::

        handler = A2ASRPCServerHandler(config)

    Where *config* is an :class:`A2ASlimRpcServerConfig` instance.
    """

    def __init__(
        self,
        config: A2ASlimRpcServerConfig,
    ):
        # BaseA2AServerHandler → ServerHandler expects (managed_object, ...)
        # We pass the config — each handler knows what its managed object is.
        super().__init__(config, transport=None, topic=None)
        self._config = config
        self._server_task: Optional[asyncio.Task] = None
        self._slim_rpc_server: Optional[slim_bindings.Server] = None
        self._slim_service: Optional[slim_bindings.Service] = None
        self._connection_id: Optional[int] = None

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
        """
        self._set_preferred_transport("slimrpc")

        # --- Apply optional card modifier ---
        if self._config.card_modifier is not None:
            self._config.agent_card = self._config.card_modifier(
                self._config.agent_card
            )

        # --- Register the event loop so Rust→Python callbacks work ---
        slim_bindings.uniffi_set_event_loop(asyncio.get_running_loop())

        # --- Build a dedicated SLIM app + connection for slimrpc ---
        # slimrpc needs its own App and connection, separate from
        # slimpatterns, for two reasons:
        #   1. A SLIM connection cannot serve both RPC and pub/sub sessions.
        #   2. Messages for the same App name get dispatched to both
        #      listeners, so sharing an App causes cross-talk.
        #
        # Strategy:
        #   - Initialise the global SLIM runtime (idempotent).
        #   - Open a *second* connection via a trailing-slash endpoint
        #     variant so the SLIM service treats it as distinct.
        #   - Create a new App under a unique internal name (the original
        #     name suffixed with "-rpc") to isolate from slimpatterns.
        #   - Pass the *original* name as the RPC Server's base_name so
        #     the subscription patterns match what the client expects.
        identity_str: str = self._config.connection.identity
        shared_secret: str = self._config.connection.shared_secret
        endpoint = self._config.connection.endpoint
        tls_insecure = self._config.connection.tls_insecure

        name = split_id(identity_str)

        # Ensure the global SLIM runtime is initialised (tracing, service,
        # etc.).  If slimpatterns already did this, it's a no-op that
        # returns the cached globals.
        service, _global_app, _global_conn = await get_or_create_slim_instance(
            local=name,
            slim_endpoint=endpoint,
            slim_insecure_client=tls_insecure,
            shared_secret=shared_secret,
        )

        # Open a dedicated connection for slimrpc by appending a trailing
        # slash so the SLIM service sees it as a distinct endpoint key.
        rpc_endpoint = endpoint.rstrip("/") + "/"
        rpc_client_config = slim_bindings.new_insecure_client_config(rpc_endpoint)
        connection_id = await service.connect_async(rpc_client_config)

        # Create a separate App under a unique name so that the SLIM
        # dataplane does not cross-deliver pub/sub messages to the RPC
        # handler (or vice-versa).
        org, ns, app = identity_str.split("/")
        rpc_app_name = slim_bindings.Name(org, ns, app + "-rpc")
        slim_app = service.create_app_with_secret(rpc_app_name, shared_secret)

        self._slim_service = service
        self._connection_id = connection_id

        # --- Create the RPC server on the dedicated connection ---
        # The base_name is the *original* name (not the "-rpc" variant)
        # so the RPC method subscriptions match what clients expect.
        self._slim_rpc_server = slim_bindings.Server.new_with_connection(
            slim_app, name, connection_id
        )

        # --- Register the A2A servicer (from slima2a) ---
        srpc_handler_kwargs = {
            "agent_card": self._config.agent_card,
            "request_handler": self._config.request_handler,
        }
        if self._config.context_builder is not None:
            srpc_handler_kwargs["context_builder"] = self._config.context_builder

        servicer = SRPCHandler(**srpc_handler_kwargs)
        add_A2AServiceServicer_to_server(servicer, self._slim_rpc_server)

        # --- Serve in background ---
        self._server_task = asyncio.create_task(
            self._slim_rpc_server.serve_async(),
            name="slimrpc-server",
        )
        logger.debug(f"slimrpc A2A handler started for identity '{identity_str}'")

    async def teardown(self) -> None:
        """Stop the slimrpc server and disconnect its dedicated SLIM connection."""
        if self._slim_rpc_server is not None:
            try:
                await self._slim_rpc_server.shutdown_async()
                logger.debug("slimrpc server shut down gracefully.")
            except Exception as e:
                logger.exception(f"Error shutting down slimrpc server: {e}")

        if self._server_task is not None and not self._server_task.done():
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            logger.debug("slimrpc server task cancelled.")

        # Disconnect the dedicated slimrpc connection (separate from the
        # global slimpatterns connection).
        if self._slim_service is not None and self._connection_id is not None:
            try:
                self._slim_service.disconnect(self._connection_id)
                logger.debug("slimrpc SLIM connection disconnected.")
            except Exception:
                pass
