# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Server-side configuration for A2A agents.

a2a-sdk 1.x removed the ``A2AStarletteApplication`` wrapper in favour of
Starlette route factories (``create_agent_card_routes`` /
``create_jsonrpc_routes``).  :class:`A2AServerConfig` is this SDK's
replacement for the object users used to hand to ``session.add(...)``: a
plain description of *what* to serve (card + request handler) that the SDK
can then expose over HTTP JSON-RPC, SLIM patterns or NATS patterns.

It mirrors :class:`~agntcy_app_sdk.semantic.a2a.server.srpc.A2ASlimRpcServerConfig`,
which plays the same role for SLIM-RPC.
"""

from __future__ import annotations

import warnings
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from a2a.types import AgentCard

if TYPE_CHECKING:
    from a2a.server.request_handlers import RequestHandler
    from starlette.applications import Starlette


@dataclass
class A2AServerConfig:
    """Describes an A2A agent to serve.

    Pass an instance to ``session.add(...)``.  With no transport the SDK
    serves it over native HTTP JSON-RPC (``.with_host()`` / ``.with_port()``
    required); with ``.with_transport(...)`` it is bridged over a SLIM or
    NATS patterns transport.

    Attributes:
        agent_card: The :class:`a2a.types.AgentCard` describing the agent.
            Transports it is served over are added to
            ``agent_card.supported_interfaces`` if not already declared.
        request_handler: The a2a-sdk ``RequestHandler`` (normally a
            ``DefaultRequestHandler``) holding the agent's business logic.
            Note that a2a-sdk 1.x requires the card at construction::

                DefaultRequestHandler(
                    agent_executor=executor,
                    task_store=InMemoryTaskStore(),
                    agent_card=agent_card,
                )
        context_builder: Optional ``ServerCallContextBuilder`` used by the
            HTTP JSON-RPC routes to build the per-request context.
        card_modifier: Optional async callable applied to the card each time
            it is served over HTTP (e.g. to inject security schemes).
        rpc_url: Path the JSON-RPC endpoint is mounted at for HTTP serving.
        enable_v0_3_compat: Also accept A2A v0.3 JSON-RPC requests on the
            HTTP endpoint.
    """

    agent_card: AgentCard
    request_handler: RequestHandler
    context_builder: Any | None = None
    card_modifier: Callable[[AgentCard], Awaitable[AgentCard]] | None = None
    rpc_url: str = "/"
    enable_v0_3_compat: bool = False

    def build_app(self) -> Starlette:
        """Build the Starlette ASGI app serving the card and JSON-RPC routes."""
        from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
        from starlette.applications import Starlette

        routes = []
        routes.extend(
            create_agent_card_routes(self.agent_card, card_modifier=self.card_modifier)
        )
        routes.extend(
            create_jsonrpc_routes(
                self.request_handler,
                rpc_url=self.rpc_url,
                context_builder=self.context_builder,
                enable_v0_3_compat=self.enable_v0_3_compat,
            )
        )
        return Starlette(routes=routes)


class A2AStarletteApplication(A2AServerConfig):
    """Deprecated compatibility shim for the removed upstream class.

    ``a2a.server.apps.A2AStarletteApplication`` no longer exists in
    a2a-sdk 1.x.  This shim keeps ``A2AStarletteApplication(agent_card=...,
    http_handler=...)`` call sites working by behaving as an
    :class:`A2AServerConfig`, but it is **deprecated** — construct an
    :class:`A2AServerConfig` instead::

        # before
        server = A2AStarletteApplication(agent_card=card, http_handler=handler)
        # after
        server = A2AServerConfig(agent_card=card, request_handler=handler)
    """

    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        **kwargs: Any,
    ) -> None:
        warnings.warn(
            "A2AStarletteApplication is deprecated; a2a-sdk 1.x removed the "
            "upstream class. Use agntcy_app_sdk.semantic.a2a.server.config."
            "A2AServerConfig(agent_card=..., request_handler=...) instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(
            agent_card=agent_card,
            request_handler=http_handler,
            **kwargs,
        )

    @property
    def http_handler(self) -> RequestHandler:
        """Deprecated alias for :attr:`request_handler`."""
        return self.request_handler

    def build(self) -> Starlette:
        """Deprecated alias for :meth:`A2AServerConfig.build_app`."""
        return self.build_app()
