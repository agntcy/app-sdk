# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any, Callable

from a2a.client.client import ClientConfig as A2AClientConfig

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

if TYPE_CHECKING:
    from agntcy_app_sdk.transport.base import BaseTransport

configure_logging()
logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-transport typed config dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SlimTransportConfig:
    """Everything needed to lazily construct a SLIMTransport.

    Required fields (``endpoint``, ``name``) are validated at construction
    time — a missing value triggers an immediate ``TypeError`` rather than
    a mysterious failure when the factory later tries to build the transport.

    Optional fields mirror the ``SLIMTransport.__init__()`` parameters and
    are forwarded as ``**kwargs`` to ``SLIMTransport.from_config()``.
    """

    endpoint: str
    """SLIM dataplane endpoint, e.g. ``"http://localhost:46357"``."""

    name: str
    """Routable name in ``"org/namespace/local_name"`` form."""

    # -- Security / auth -----------------------------------------------------

    shared_secret_identity: str = "slim-mls-secret-REPLACE_WITH_RANDOM_32PLUS_CHARS"
    """MLS shared secret.  Must be ≥ 32 characters for SLIM v0.7+."""

    tls_insecure: bool = True
    """Skip TLS certificate verification."""

    jwt: str | None = None
    """JWT token for authentication."""

    bundle: str | None = None
    """Auth bundle."""

    audience: list[str] | None = None
    """JWT audience list."""

    # -- Timeouts / retries --------------------------------------------------

    message_timeout_seconds: float = 60.0
    """Timeout (in seconds) for listening for sessions."""

    message_retries: int = 2
    """Max retries on receive errors before giving up."""


@dataclasses.dataclass
class NatsTransportConfig:
    """Everything needed to lazily construct a NatsTransport.

    Optional fields mirror the ``NatsTransport.__init__()`` kwargs.
    """

    endpoint: str
    """NATS server endpoint, e.g. ``"nats://localhost:4222"``."""

    # -- Connection options ---------------------------------------------------

    connect_timeout: int = 5
    """Timeout (in seconds) for the initial connection."""

    reconnect_time_wait: int = 2
    """Seconds to wait between reconnect attempts."""

    max_reconnect_attempts: int = 30
    """Maximum number of reconnection attempts."""

    drain_timeout: int = 2
    """Timeout (in seconds) for draining the connection on close."""


@dataclasses.dataclass
class SlimRpcConfig:
    """Everything needed to lazily construct SLIM-RPC infrastructure.

    When set on :class:`ClientConfig`, the factory will call
    ``setup_slim_client(namespace, group, name)`` only if the AgentCard
    negotiation selects ``slimrpc`` as the winning transport.
    """

    namespace: str
    group: str
    name: str


# ---------------------------------------------------------------------------
# Extended ClientConfig
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class ClientConfig(A2AClientConfig):
    """Extended A2A client config with deferred and eager transport fields.

    For each transport there are two optional fields:

    * **Deferred** (``*_config``) — a typed dataclass holding the parameters
      needed to construct the transport.  Nothing is instantiated; the factory
      builds the transport lazily only when the AgentCard selects it.
    * **Eager** (``*_transport`` / ``*_channel_factory``) — a pre-built
      transport or factory callable.  Use this when you already have a live
      transport instance (e.g. shared with other parts of your application).

    If neither field is set for a transport, that transport is unavailable.

    ``supported_transports`` is auto-derived in ``__post_init__`` from
    whichever fields are populated — you should not need to set it manually.
    """

    # -- SLIM-RPC (protobuf-over-SLIM, via slima2a) --------------------------

    slimrpc_config: SlimRpcConfig | None = None
    """Deferred: parameters for lazy SLIM-RPC setup."""

    slimrpc_channel_factory: Callable[[str], Any] | None = None
    """Eager: a ``(url) -> Channel`` callable for ``SRPCTransport``."""

    # -- SLIM patterns -------------------------------------------------------

    slim_config: SlimTransportConfig | None = None
    """Deferred: parameters for lazy ``SLIMTransport`` construction."""

    slim_transport: BaseTransport | None = None
    """Eager: a pre-built ``SLIMTransport`` instance."""

    # -- NATS patterns -------------------------------------------------------

    nats_config: NatsTransportConfig | None = None
    """Deferred: parameters for lazy ``NatsTransport`` construction."""

    nats_transport: BaseTransport | None = None
    """Eager: a pre-built ``NatsTransport`` instance."""

    # -- Auto-derive supported_transports ------------------------------------

    def __post_init__(self) -> None:
        """Populate ``supported_transports`` from configured fields.

        Only runs when the user has *not* explicitly set
        ``supported_transports``.  JSONRPC is always included as a fallback.
        """
        if not self.supported_transports:
            transports: list[str] = ["JSONRPC"]
            if self.slim_config is not None or self.slim_transport is not None:
                transports.append("slimpatterns")
            if self.nats_config is not None or self.nats_transport is not None:
                transports.append("natspatterns")
            if (
                self.slimrpc_config is not None
                or self.slimrpc_channel_factory is not None
            ):
                transports.append("slimrpc")
            self.supported_transports = transports
