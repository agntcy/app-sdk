# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Card-driven multi-transport server bootstrap for A2A agents.

Provides :func:`serve_card` which reads an ``AgentCard``'s
``additional_interfaces``, parses each URL into transport-specific config,
and starts all sessions — replacing ~50 lines of per-agent boilerplate
with a single call.

Example::

    factory = AgntcyFactory("lungo.brazil_farm", enable_tracing=True)
    session = factory.create_app_session()
    await session.serve_card(
        agent_card=agent_card,
        request_handler=request_handler,
        keep_alive=True,
    )

URL formats
-----------
Each transport accepts **two** URL styles:

**Topic-only** (compatible with ``create_transport_uri`` / client cards)::

    AgentInterface(transport="slimpatterns", url="slim://my_topic")
    AgentInterface(transport="natspatterns", url="nats://my_topic")
    AgentInterface(transport="slimrpc",     url="slim://org/ns/agent")

Endpoint is resolved from ``SLIM_ENDPOINT`` / ``NATS_ENDPOINT`` env vars,
defaulting to ``localhost`` with the standard port.

**Explicit endpoint** (endpoint + path encoded in one URL)::

    AgentInterface(transport="slimpatterns", url="slim://host:46357/my_topic")
    AgentInterface(transport="natspatterns", url="nats://host:4222/my_topic")
    AgentInterface(transport="slimrpc",     url="slim://host:46357/org/ns/agent")

Detection heuristic: if ``urlparse`` finds a port **or** a non-empty path,
the URL is treated as ``scheme://host[:port]/path``; otherwise the
authority segment is the topic/identity itself.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from agntcy_app_sdk.common.logging_config import get_logger

if TYPE_CHECKING:
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.types import AgentCard, AgentInterface

    from agntcy_app_sdk.app_sessions import AppSession
    from agntcy_app_sdk.factory import AgntcyFactory

logger = get_logger(__name__)

# Default endpoints when using topic-only URLs
_SLIM_DEFAULT_ENDPOINT = "http://localhost:46357"
_NATS_DEFAULT_ENDPOINT = "nats://localhost:4222"


# ---------------------------------------------------------------------------
# Interface transport types — valid values for ``AgentInterface.transport``
# ---------------------------------------------------------------------------


class InterfaceTransport:
    """Valid transport identifiers for ``AgentInterface.transport``.

    Use these constants instead of hard-coded strings when building
    ``AgentCard.additional_interfaces``::

        from agntcy_app_sdk import InterfaceTransport

        AgentInterface(
            transport=InterfaceTransport.SLIM_PATTERNS,
            url="slim://topic",
        )
        AgentInterface(
            transport=InterfaceTransport.JSONRPC,
            url="http://0.0.0.0:9999",
        )

    **Aliases** are provided for convenience — they resolve to the same
    canonical transport during parsing:

    ================================  =====================
    Alias                             Resolves to
    ================================  =====================
    ``InterfaceTransport.SLIM``         ``"slimpatterns"``
    ``InterfaceTransport.NATS``         ``"natspatterns"``
    ``InterfaceTransport.SLIM_EXTENDED`` ``"slimpatterns"``
    ================================  =====================

    Call :meth:`all_types` to retrieve the full set of accepted strings.
    """

    # -- Canonical types (one-to-one with internal transport dispatch) ------

    SLIM_RPC: str = "slimrpc"
    """SLIM-RPC (protobuf-over-SLIM) transport."""

    SLIM_PATTERNS: str = "slimpatterns"
    """SLIM pub/sub patterns transport (A2A experimental)."""

    NATS_PATTERNS: str = "natspatterns"
    """NATS pub/sub patterns transport (A2A experimental)."""

    JSONRPC: str = "jsonrpc"
    """HTTP JSON-RPC transport (standard A2A)."""

    HTTP: str = "http"
    """HTTP transport (alias for ``jsonrpc``)."""

    # -- Convenience aliases ------------------------------------------------

    SLIM: str = "slimpatterns"
    """Alias for :attr:`SLIM_PATTERNS`."""

    NATS: str = "natspatterns"
    """Alias for :attr:`NATS_PATTERNS`."""

    SLIM_EXTENDED: str = "slimpatterns"
    """Alias for :attr:`SLIM_PATTERNS` (emphasises extended A2A features)."""

    @classmethod
    def all_types(cls) -> set[str]:
        """Return the full set of strings accepted as transport types.

        Includes canonical names **and** aliases, all lower-case.
        """
        return set(_TRANSPORT_ALIASES.keys()) | _CANONICAL_TRANSPORTS

    @classmethod
    def canonical_types(cls) -> set[str]:
        """Return only the canonical (non-alias) transport types."""
        return set(_CANONICAL_TRANSPORTS)


# Canonical transports (used for dispatch after normalisation)
_CANONICAL_TRANSPORTS: frozenset[str] = frozenset(
    {
        "slimrpc",
        "slimpatterns",
        "natspatterns",
        "jsonrpc",
        "http",
    }
)

# Alias → canonical mapping (lower-case keys)
_TRANSPORT_ALIASES: dict[str, str] = {
    "slim": "slimpatterns",
    "slim-extended": "slimpatterns",
    "nats": "natspatterns",
}


def _normalize_transport(raw: str) -> str:
    """Normalise a transport identifier to its canonical form.

    Applies case-folding and alias resolution.  Returns the canonical
    string or the lower-cased input if it is already canonical.
    """
    key = raw.lower()
    return _TRANSPORT_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------


def _resolve_slim_endpoint() -> str:
    """Return the SLIM endpoint from ``SLIM_ENDPOINT`` or the default."""
    return os.environ.get("SLIM_ENDPOINT", _SLIM_DEFAULT_ENDPOINT)


def _resolve_nats_endpoint() -> str:
    """Return the NATS endpoint from ``NATS_ENDPOINT`` or the default."""
    return os.environ.get("NATS_ENDPOINT", _NATS_DEFAULT_ENDPOINT)


def _has_explicit_endpoint(parsed_url: object) -> bool:
    """Determine whether a ``urlparse`` result contains a real host:port.

    Returns ``True`` when the URL has an explicit port **or** a non-empty
    path — indicating the authority section is a network address, not a
    topic/identity string.

    Examples:
        ``slim://host:46357/topic``  →  port=46357, path="/topic" → True
        ``slim://my_topic``          →  port=None, path=""        → False
        ``slim://org/ns/name``       →  port=None, path="/ns/name" → True
        ``nats://host:4222/topic``   →  port=4222, path="/topic"  → True
        ``nats://my_topic``          →  port=None, path=""        → False
    """
    p = parsed_url  # type: ignore[assignment]
    return p.port is not None or bool(p.path and p.path != "/")


# ---------------------------------------------------------------------------
# URL parsing — public API
# ---------------------------------------------------------------------------


def parse_interface_url(interface: AgentInterface) -> dict[str, str | int]:
    """Parse an ``AgentInterface`` URL into transport-specific config values.

    Supports **two URL styles** per transport:

    - **Topic-only** (``slim://my_topic``, ``nats://my_topic``,
      ``slim://org/ns/name``) — endpoint resolved from env vars or defaults.
    - **Explicit endpoint** (``slim://host:46357/my_topic``,
      ``nats://host:4222/topic``) — endpoint extracted from the URL.

    Returns a dict whose keys depend on the transport type:

    - **slimrpc**: ``{"endpoint": ..., "identity": ...}``
    - **slim / slimpatterns**: ``{"endpoint": ..., "topic": ...}``
    - **nats / natspatterns**: ``{"endpoint": ..., "topic": ...}``
    - **jsonrpc / http**: ``{"host": ..., "port": ...}``

    Raises:
        ValueError: If the URL cannot be parsed for the given transport.
    """
    transport_type = _normalize_transport(interface.transport)
    url = interface.url
    parsed = urlparse(url)

    if transport_type == "slimrpc":
        return _parse_slimrpc(url, parsed)

    if transport_type == "slimpatterns":
        return _parse_slim_patterns(url, parsed)

    if transport_type == "natspatterns":
        return _parse_nats_patterns(url, parsed)

    if transport_type in ("jsonrpc", "http"):
        host = parsed.hostname or "0.0.0.0"
        port = parsed.port or 9000
        return {"host": host, "port": port}

    raise ValueError(f"Unknown transport type: {transport_type!r}")


def _parse_slimrpc(url: str, parsed: object) -> dict[str, str | int]:
    """Parse a slimrpc URL.

    Accepted formats::

        slim://org/ns/agent_name            → topic-only (identity = org/ns/agent_name)
        slim://host:46357/org/ns/agent_name → explicit endpoint
    """
    p = parsed  # type: ignore[assignment]

    if _has_explicit_endpoint(p) and p.port is not None:
        # Explicit: slim://host:port/org/ns/agent_name
        host = p.hostname or "localhost"
        endpoint = f"http://{host}:{p.port}"
        identity = p.path.lstrip("/")
    else:
        # Topic-only: slim://org/ns/agent_name
        # urlparse sees hostname="org", path="/ns/agent_name"
        endpoint = _resolve_slim_endpoint()
        hostname = p.hostname or ""
        path = p.path.lstrip("/")
        identity = f"{hostname}/{path}" if path else hostname

    if not identity:
        raise ValueError(f"slimrpc URL must include an identity path: {url!r}")

    return {"endpoint": endpoint, "identity": identity}


def _parse_slim_patterns(url: str, parsed: object) -> dict[str, str | int]:
    """Parse a slim/slimpatterns URL.

    Accepted formats::

        slim://my_topic            → topic-only
        slim://host:46357/my_topic → explicit endpoint
    """
    p = parsed  # type: ignore[assignment]

    if _has_explicit_endpoint(p):
        # Explicit: slim://host[:port]/topic
        host = p.hostname or "localhost"
        port = p.port or 46357
        endpoint = f"http://{host}:{port}"
        topic = p.path.lstrip("/")
    else:
        # Topic-only: slim://my_topic
        endpoint = _resolve_slim_endpoint()
        topic = p.hostname or ""

    if not topic:
        raise ValueError(f"slim URL must include a topic: {url!r}")

    return {"endpoint": endpoint, "topic": topic}


def _parse_nats_patterns(url: str, parsed: object) -> dict[str, str | int]:
    """Parse a nats/natspatterns URL.

    Accepted formats::

        nats://my_topic            → topic-only
        nats://host:4222/my_topic  → explicit endpoint
    """
    p = parsed  # type: ignore[assignment]

    if _has_explicit_endpoint(p):
        # Explicit: nats://host[:port]/topic
        host = p.hostname or "localhost"
        port = p.port or 4222
        endpoint = f"nats://{host}:{port}"
        topic = p.path.lstrip("/")
    else:
        # Topic-only: nats://my_topic
        endpoint = _resolve_nats_endpoint()
        topic = p.hostname or ""

    if not topic:
        raise ValueError(f"nats URL must include a topic: {url!r}")

    return {"endpoint": endpoint, "topic": topic}


# ---------------------------------------------------------------------------
# Dry-run summary
# ---------------------------------------------------------------------------


@dataclass
class ServeCardPlan:
    """Describes what :func:`serve_card` *would* start (dry-run output).

    Each entry in :attr:`containers` maps a session ID to a human-readable
    description of the transport and its configuration.
    """

    containers: list[dict[str, str]] = field(default_factory=list)

    def __str__(self) -> str:
        if not self.containers:
            return "No containers to serve."
        lines = ["serve_card plan:"]
        for entry in self.containers:
            lines.append(
                f"  [{entry['session_id']}] {entry['transport']} -> {entry['detail']}"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# serve_card core loop
# ---------------------------------------------------------------------------


async def serve_card(
    session: AppSession,
    factory: AgntcyFactory,
    agent_card: AgentCard,
    request_handler: DefaultRequestHandler,
    *,
    keep_alive: bool = False,
    dry_run: bool = False,
) -> ServeCardPlan | None:
    """Read *agent_card.additional_interfaces* and start all transports.

    For each ``AgentInterface`` declared on the card, this function:

    1. Parses the URL into transport-specific config.
    2. Creates the appropriate transport / config objects.
    3. Registers an ``AppContainer`` on *session* via the fluent builder API.
    4. After all containers are registered, calls
       ``session.start_all_sessions(keep_alive=keep_alive)``.

    Args:
        session: The ``AppSession`` that will own the containers.
        factory: An ``AgntcyFactory`` used to create transports.
        agent_card: The agent card whose ``additional_interfaces`` list
            describes the transports to start.
        request_handler: The ``DefaultRequestHandler`` with the agent's
            business logic.
        keep_alive: If *True*, block on a shutdown signal after starting.
        dry_run: If *True*, do **not** create any containers or start any
            sessions.  Instead return a :class:`ServeCardPlan` describing
            what *would* be started.  Useful for validation and debugging.

    Returns:
        ``None`` when *dry_run* is ``False`` (normal operation).
        A :class:`ServeCardPlan` when *dry_run* is ``True``.

    Raises:
        ValueError: If ``additional_interfaces`` is empty/missing, or if a
            required environment variable (e.g. ``SLIM_SHARED_SECRET``) is
            not set for a declared interface.
    """
    from a2a.server.apps import A2AStarletteApplication

    from agntcy_app_sdk.semantic.a2a.server.srpc import (
        A2ASlimRpcServerConfig,
        SlimRpcConnectionConfig,
    )

    interfaces = agent_card.additional_interfaces or []
    if not interfaces:
        raise ValueError("agent_card.additional_interfaces is empty; nothing to serve")

    plan = ServeCardPlan()

    # Build the A2AStarletteApplication once for transports that need it
    # (slim/nats patterns and jsonrpc all share the same app instance).
    a2a_app = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    for i, interface in enumerate(interfaces):
        transport_type = _normalize_transport(interface.transport)

        if transport_type not in _CANONICAL_TRANSPORTS:
            logger.warning(
                "Unknown transport type '%s' on interface %d, skipping",
                interface.transport,
                i,
            )
            continue

        parsed = parse_interface_url(interface)

        if transport_type == "slimrpc":
            shared_secret = os.environ.get("SLIM_SHARED_SECRET")
            if not shared_secret:
                raise ValueError(
                    "SLIM_SHARED_SECRET env var required for slimrpc interface"
                )
            session_id = f"slimrpc-{i}"

            if dry_run:
                plan.containers.append(
                    {
                        "session_id": session_id,
                        "transport": "slimrpc",
                        "detail": (
                            f"endpoint={parsed['endpoint']}, "
                            f"identity={parsed['identity']}"
                        ),
                    }
                )
                continue

            config = A2ASlimRpcServerConfig(
                agent_card=agent_card,
                request_handler=request_handler,
                connection=SlimRpcConnectionConfig(
                    identity=str(parsed["identity"]),
                    shared_secret=shared_secret,
                    endpoint=str(parsed["endpoint"]),
                ),
            )
            session.add(config).with_session_id(session_id).build()

        elif transport_type == "slimpatterns":
            shared_secret = os.environ.get("SLIM_SHARED_SECRET")
            if not shared_secret:
                raise ValueError(
                    "SLIM_SHARED_SECRET env var required for slim interface"
                )
            session_id = f"slim-{i}"
            # Use the topic from the interface URL as the SLIM routable
            # name — in SLIM, the transport name IS the routing address
            # that clients send to.
            routable_name = str(parsed["topic"])

            if dry_run:
                plan.containers.append(
                    {
                        "session_id": session_id,
                        "transport": transport_type,
                        "detail": (
                            f"endpoint={parsed['endpoint']}, "
                            f"topic={parsed['topic']}, "
                            f"name={routable_name}"
                        ),
                    }
                )
                continue

            transport = factory.create_transport(
                "SLIM",
                endpoint=str(parsed["endpoint"]),
                name=routable_name,
                shared_secret_identity=shared_secret,
            )
            (
                session.add(a2a_app)
                .with_transport(transport)
                .with_topic(str(parsed["topic"]))
                .with_session_id(session_id)
                .build()
            )

        elif transport_type == "natspatterns":
            session_id = f"nats-{i}"

            if dry_run:
                plan.containers.append(
                    {
                        "session_id": session_id,
                        "transport": transport_type,
                        "detail": (
                            f"endpoint={parsed['endpoint']}, "
                            f"topic={parsed['topic']}"
                        ),
                    }
                )
                continue

            transport = factory.create_transport(
                "NATS",
                endpoint=str(parsed["endpoint"]),
            )
            (
                session.add(a2a_app)
                .with_transport(transport)
                .with_topic(str(parsed["topic"]))
                .with_session_id(session_id)
                .build()
            )

        elif transport_type in ("jsonrpc", "http"):
            session_id = f"http-{i}"

            if dry_run:
                plan.containers.append(
                    {
                        "session_id": session_id,
                        "transport": transport_type,
                        "detail": f"host={parsed['host']}, port={parsed['port']}",
                    }
                )
                continue

            (
                session.add(a2a_app)
                .with_host(str(parsed["host"]))
                .with_port(int(parsed["port"]))
                .with_session_id(session_id)
                .build()
            )

    if dry_run:
        return plan

    await session.start_all_sessions(keep_alive=keep_alive)
    return None
