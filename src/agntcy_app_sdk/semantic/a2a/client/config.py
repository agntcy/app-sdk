# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.types import AgentCard

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


@dataclasses.dataclass
class ClientConfig(A2AClientConfig):
    """Extended client config that adds transport factory callables for
    SLIM-patterns, NATS-patterns, and SLIM-RPC transports.

    These factory callables are invoked by ``PatternsClientTransport.create()``
    (or ``SRPCTransport.create()``) during upstream ``ClientFactory`` transport
    negotiation.  Each factory returns a ready-to-use object:

    * ``slimrpc_channel_factory`` — ``Callable[[str], Channel]``
      Produces a ``slim_bindings.Channel`` given a remote address string.
      Consumed by ``slima2a.client_transport.SRPCTransport.create()``.
    * ``slim_patterns_transport_factory`` — ``Callable[..., BaseTransport]``
      Returns a fully-constructed SLIM ``BaseTransport`` instance.
    * ``nats_transport_factory`` — ``Callable[..., BaseTransport]``
      Returns a fully-constructed NATS ``BaseTransport`` instance.
    """

    slimrpc_channel_factory: Callable[[str], Any] | None = None
    slim_patterns_transport_factory: Callable[..., Any] | None = None
    nats_transport_factory: Callable[..., Any] | None = None

    @classmethod
    def from_card(cls, card: AgentCard, **kwargs: Any) -> ClientConfig:
        """Auto-configure transport support based on an AgentCard.

        Reads ``card.preferred_transport`` and ``card.additional_interfaces``
        to build the ``supported_transports`` list.  The actual factory
        callables (``slim_patterns_transport_factory``, etc.) are left as
        ``None`` — the caller must populate them manually after construction.

        NOTE: How ``from_card()`` sources infra connection details (SLIM
        endpoint, shared secret, NATS URL) is deferred — needs a design
        interview.  For now the method just reads transport types from the
        card.

        Args:
            card: The ``AgentCard`` to derive transport support from.
            **kwargs: Forwarded to the ``ClientConfig`` constructor (e.g.
                ``streaming``, ``httpx_client``, factory callables, etc.).

        Returns:
            A new ``ClientConfig`` with ``supported_transports`` populated.
        """
        transports: list[str] = []

        if card.preferred_transport:
            transports.append(card.preferred_transport)

        if card.additional_interfaces:
            for iface in card.additional_interfaces:
                if iface.transport and iface.transport not in transports:
                    transports.append(iface.transport)

        # Always include JSONRPC as a fallback
        if "JSONRPC" not in transports:
            transports.append("JSONRPC")

        config = cls(supported_transports=transports, **kwargs)

        logger.debug(
            "ClientConfig.from_card: supported_transports=%s for card=%s",
            transports,
            card.name,
        )
        return config
