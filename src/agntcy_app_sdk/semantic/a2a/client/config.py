# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import dataclasses
import warnings
from typing import TYPE_CHECKING, Any, Callable, Literal

from a2a.client.client import ClientConfig as A2AClientConfig

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.card_utils import wire_binding

if TYPE_CHECKING:
    from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Per-transport typed config dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class SlimRpcConfig:
    """Fields needed to lazily construct SLIM-RPC infrastructure.

    Note that this is an optional part of the config — you can set up SLIM-RPC eagerly with
    a pre-built channel factory instead.

    When set on :class:`ClientConfig`, the factory will call
    ``setup_slim_client(namespace, group, name, slim_url, ...)`` only if the
    AgentCard negotiation selects ``slimrpc`` as the winning transport.
    """

    namespace: str
    group: str
    name: str

    slim_url: str = "http://localhost:46357"
    """SLIM dataplane endpoint."""

    secret: str = "secretsecretsecretsecretsecretsecret"
    """Shared secret for SLIM authentication."""

    log_level: Literal["trace", "debug", "info", "warn", "error"] = "info"
    """Log level for the underlying SLIM client."""


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

    ``supported_protocol_bindings`` (the a2a-sdk 1.x name for the ordered
    list of transports the client can use) is auto-derived in
    ``__post_init__`` from whichever fields are populated — you should not
    need to set it manually.  Entries are normalised to the casing the
    upstream factory expects (``"jsonrpc"`` → ``"JSONRPC"``) and aliases are
    resolved (``"slim"`` → ``"slimpatterns"``).
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

    # -- Deprecated ----------------------------------------------------------

    supported_transports: list[str] | None = None
    """**Deprecated** — use ``supported_protocol_bindings``.

    a2a-sdk 1.x renamed this field.  A value passed here is copied into
    ``supported_protocol_bindings`` (with a ``DeprecationWarning``); when
    nothing was passed it mirrors the resolved bindings for backward
    compatibility.
    """

    # -- Auto-derive supported_protocol_bindings -----------------------------

    def __post_init__(self) -> None:
        """Populate ``supported_protocol_bindings`` from configured fields.

        Only derives a list when the user has *not* explicitly set
        ``supported_protocol_bindings`` (or the deprecated
        ``supported_transports``).  JSONRPC is always included as a fallback.
        """
        if self.supported_transports and not self.supported_protocol_bindings:
            warnings.warn(
                "ClientConfig.supported_transports is deprecated; use "
                "supported_protocol_bindings (renamed in a2a-sdk 1.x).",
                DeprecationWarning,
                stacklevel=3,
            )
            self.supported_protocol_bindings = list(self.supported_transports)

        if not self.supported_protocol_bindings:
            bindings: list[str] = ["JSONRPC"]
            if self.slim_config is not None or self.slim_transport is not None:
                bindings.append("slimpatterns")
            if self.nats_config is not None or self.nats_transport is not None:
                bindings.append("natspatterns")
            if (
                self.slimrpc_config is not None
                or self.slimrpc_channel_factory is not None
            ):
                bindings.append("slimrpc")
            self.supported_protocol_bindings = bindings
        else:
            self.supported_protocol_bindings = [
                wire_binding(b) for b in self.supported_protocol_bindings
            ]

        # Keep the deprecated mirror in sync for code that still reads it.
        self.supported_transports = list(self.supported_protocol_bindings)
