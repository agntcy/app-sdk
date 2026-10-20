# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from dataclasses import dataclass
from typing import Any, Callable, Optional, cast

import slim_bindings

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.types import AgentCard
from a2a.types import a2a_pb2
from google.protobuf import empty_pb2

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import declare_identity_auth
from agntcy_app_sdk.semantic.a2a.server.base import BaseA2AServerHandler
from agntcy_app_sdk.semantic.a2a.server.experimental_patterns import (
    check_identity_token,
)
from agntcy_app_sdk.transport.slim.common import (
    get_or_init_slim_service,
    new_client_config,
    resolve_shared_secret,
    split_id,
)

from identityservice.sdk import IdentityServiceSdk
from slima2a.handler import CallContextBuilder, SRPCHandler
from slima2a.types.v1.a2a_pb2_slimrpc import add_A2AServiceServicer_to_server

logger = get_logger(__name__)

_REJECTION_CODES = {
    401: slim_bindings.RpcCode.UNAUTHENTICATED,
    403: slim_bindings.RpcCode.PERMISSION_DENIED,
}


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
    """Connect to the SLIM dataplane in plaintext.  Set ``False`` for TLS 1.3
    with certificate verification against the system CA pool."""


@dataclass
class A2ASlimRpcServerConfig:
    """Configuration object for the slimrpc-based A2A handler.

    Users pass an instance of this to ``session.add(config)`` instead of
    an :class:`~agntcy_app_sdk.semantic.a2a.server.config.A2AServerConfig`.
    The SDK will internally create the ``SRPCHandler`` and
    ``slim_bindings.Server``.

    Required fields:
        agent_card: The A2A AgentCard describing this agent.
        request_handler: A ``DefaultRequestHandler`` (from ``a2a-sdk``)
            that implements the agent's business logic.
        connection: A :class:`SlimRpcConnectionConfig` with SLIM
            connectivity parameters (identity, shared_secret, etc.).

    Optional fields:
        context_builder: Optional ``slima2a.handler.CallContextBuilder`` used
            to build the per-request context.
        card_modifier: Optional callable to modify the agent card before
            it is published (e.g. add security schemes).
    """

    agent_card: AgentCard
    request_handler: DefaultRequestHandler
    connection: SlimRpcConnectionConfig
    context_builder: CallContextBuilder | None = None
    card_modifier: Optional[Callable[[AgentCard], AgentCard]] = None


# ---------------------------------------------------------------------------
# Identity auth
# ---------------------------------------------------------------------------


class _IdentityCheckedSRPCHandler(SRPCHandler):
    """``SRPCHandler`` that checks the caller's Identity Service token first.

    The SDK's slimrpc client sends the token as call metadata.  slima2a
    treats metadata as caller-supplied, not as an identity; here it is only
    a bearer token that the Identity Service verifies on every call.  A
    rejected call fails with ``UNAUTHENTICATED`` (no or malformed token) or
    ``PERMISSION_DENIED`` before it reaches the request handler.
    """

    def __init__(self, *args: Any, identity_sdk: IdentityServiceSdk, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._identity_sdk = identity_sdk

    async def _check(self, context: slim_bindings.Context) -> None:
        header = next(
            (v for k, v in context.metadata().items() if k.lower() == "authorization"),
            None,
        )
        rejection = await check_identity_token(self._identity_sdk, header)
        if rejection is not None:
            reason, status = rejection
            raise slim_bindings.RpcError.Rpc(  # type: ignore[attr-defined]
                code=_REJECTION_CODES[status], message=reason, details=None
            )

    async def SendMessage(
        self, request: a2a_pb2.SendMessageRequest, context: slim_bindings.Context
    ) -> a2a_pb2.SendMessageResponse:
        await self._check(context)
        return await super().SendMessage(request, context)

    async def SendStreamingMessage(
        self, request: a2a_pb2.SendMessageRequest, context: slim_bindings.Context
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        await self._check(context)
        async for event in super().SendStreamingMessage(request, context):
            yield event

    async def CancelTask(
        self, request: a2a_pb2.CancelTaskRequest, context: slim_bindings.Context
    ) -> a2a_pb2.Task:
        await self._check(context)
        return await super().CancelTask(request, context)

    async def SubscribeToTask(
        self, request: a2a_pb2.SubscribeToTaskRequest, context: slim_bindings.Context
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        await self._check(context)
        async for event in super().SubscribeToTask(request, context):
            yield event

    async def GetTask(
        self, request: a2a_pb2.GetTaskRequest, context: slim_bindings.Context
    ) -> a2a_pb2.Task:
        await self._check(context)
        return await super().GetTask(request, context)

    async def ListTasks(
        self, request: a2a_pb2.ListTasksRequest, context: slim_bindings.Context
    ) -> a2a_pb2.ListTasksResponse:
        await self._check(context)
        return await super().ListTasks(request, context)

    async def CreateTaskPushNotificationConfig(
        self,
        request: a2a_pb2.TaskPushNotificationConfig,
        context: slim_bindings.Context,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        await self._check(context)
        return cast(
            a2a_pb2.TaskPushNotificationConfig,
            await super().CreateTaskPushNotificationConfig(request, context),
        )

    async def GetTaskPushNotificationConfig(
        self,
        request: a2a_pb2.GetTaskPushNotificationConfigRequest,
        context: slim_bindings.Context,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        await self._check(context)
        return await super().GetTaskPushNotificationConfig(request, context)

    async def ListTaskPushNotificationConfigs(
        self,
        request: a2a_pb2.ListTaskPushNotificationConfigsRequest,
        context: slim_bindings.Context,
    ) -> a2a_pb2.ListTaskPushNotificationConfigsResponse:
        await self._check(context)
        return await super().ListTaskPushNotificationConfigs(request, context)

    async def DeleteTaskPushNotificationConfig(
        self,
        request: a2a_pb2.DeleteTaskPushNotificationConfigRequest,
        context: slim_bindings.Context,
    ) -> empty_pb2.Empty:
        await self._check(context)
        return await super().DeleteTaskPushNotificationConfig(request, context)

    async def GetExtendedAgentCard(
        self,
        request: a2a_pb2.GetExtendedAgentCardRequest,
        context: slim_bindings.Context,
    ) -> a2a_pb2.AgentCard:
        await self._check(context)
        return await super().GetExtendedAgentCard(request, context)


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
        self._server_task: Optional[asyncio.Task[None]] = None
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
        1. Declare a ``slimrpc`` interface on the agent card (if absent).
        2. Create a SLIM ``App`` + ``Server`` from the user-provided config.
        3. Import ``slima2a`` and register the A2A servicer.  With identity
           auth enabled, the servicer checks each call's token, and setup
           raises if the Identity Service SDK cannot be built.
        4. Launch ``server.serve_async()`` as a background task.
        """
        # slima2a clients hand the interface URL straight to the channel
        # factory, which expects a bare "org/ns/name" identity.  A newly
        # declared slimrpc interface becomes the preferred transport.
        self._declare_interface(
            "slimrpc", self._config.connection.identity, prefer=True
        )

        # --- Apply optional card modifier ---
        if self._config.card_modifier is not None:
            self._config.agent_card = self._config.card_modifier(
                self._config.agent_card
            )

        # --- Register the event loop so Rust→Python callbacks work ---
        slim_bindings.uniffi_set_event_loop(
            cast(asyncio.BaseEventLoop, asyncio.get_running_loop())
        )

        # --- Build a dedicated SLIM app + connection for slimrpc ---
        # slimrpc needs its own App and connection, separate from
        # slimpatterns, for two reasons:
        #   1. A SLIM connection cannot serve both RPC and pub/sub sessions.
        #   2. Messages for the same App name get dispatched to both
        #      listeners, so sharing an App causes cross-talk.
        #
        # Strategy:
        #   - Initialise the global SLIM runtime (idempotent) and take the
        #     service; no pub/sub App is created or subscribed.
        #   - Open a dedicated connection via a trailing-slash endpoint
        #     variant so the SLIM service treats it as distinct.
        #   - Create a new App under a unique internal name (the original
        #     name suffixed with "-rpc").
        #   - Pass the *original* name as the RPC Server's base_name so
        #     the subscription patterns match what the client expects.
        identity_str: str = self._config.connection.identity
        shared_secret = resolve_shared_secret(self._config.connection.shared_secret)
        endpoint = self._config.connection.endpoint

        name = split_id(identity_str)

        # Built before anything connects, so a server that cannot check
        # tokens fails here instead of serving unchecked calls.
        identity_sdk: IdentityServiceSdk | None = None
        if is_identity_auth_enabled():
            identity_sdk = IdentityServiceSdk()
            declare_identity_auth(self._config.agent_card)
            logger.info("Identity auth enabled for slimrpc calls to '%s'", identity_str)

        # Ensure the global SLIM runtime is initialised and grab the service.
        # We deliberately do NOT use ``get_or_create_slim_instance`` here: it
        # subscribes a pub/sub App under ``name``, and SLIM 2.x would route
        # the clients' RPC sessions to that App (which has no RPC handler)
        # instead of the RPC server below.
        service = get_or_init_slim_service()

        # Open a dedicated connection for slimrpc by appending a trailing
        # slash so the SLIM service sees it as a distinct endpoint key.
        rpc_endpoint = endpoint.rstrip("/") + "/"
        rpc_client_config = new_client_config(
            rpc_endpoint, insecure=self._config.connection.tls_insecure
        )
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
        servicer = (
            SRPCHandler(
                agent_card=self._config.agent_card,
                request_handler=self._config.request_handler,
                context_builder=self._config.context_builder,
            )
            if identity_sdk is None
            else _IdentityCheckedSRPCHandler(
                agent_card=self._config.agent_card,
                request_handler=self._config.request_handler,
                context_builder=self._config.context_builder,
                identity_sdk=identity_sdk,
            )
        )
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
