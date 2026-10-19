# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Card-driven multi-transport server bootstrap for A2A agents.

Provides :class:`CardBuilder` which reads an ``AgentCard``'s
``supported_interfaces``, parses each URL into transport-specific config,
and starts all sessions via a fluent API.

Example::

    factory = AgntcyFactory("lungo.brazil_farm", enable_tracing=True)
    session = factory.create_app_session()
    await (
        session.add_a2a_card(agent_card, request_handler)
        .with_factory(factory)
        .start(keep_alive=True)
    )

URL formats
-----------
Each transport accepts **two** URL styles:

**Topic-only** (compatible with ``create_transport_uri`` / client cards)::

    AgentInterface(protocol_binding="slimpatterns", url="slim://my_topic")
    AgentInterface(protocol_binding="slimpatterns", url="slim://org/ns/agent")
    AgentInterface(protocol_binding="natspatterns", url="nats://my_topic")
    AgentInterface(protocol_binding="slimrpc",     url="slim://org/ns/agent")

Endpoint is resolved from ``SLIM_ENDPOINT`` / ``NATS_ENDPOINT`` env vars,
defaulting to ``localhost`` with the standard port.

**Explicit endpoint** (endpoint + path encoded in one URL)::

    AgentInterface(protocol_binding="slimpatterns", url="slim://host:46357/my_topic")
    AgentInterface(protocol_binding="slimpatterns", url="slim://host:46357/org/ns/agent")
    AgentInterface(protocol_binding="natspatterns", url="nats://host:4222/my_topic")
    AgentInterface(protocol_binding="slimrpc",     url="slim://host:46357/org/ns/agent")

Detection heuristic:

- **SLIM** (``slim``, ``slimpatterns``, ``slimrpc``): a port marks an
  explicit endpoint. Without a port the whole remainder is the name, so
  ``slim://org/ns/agent`` is the identity ``org/ns/agent`` on the default
  dataplane. A path alone is not a host.
- **NATS**: a port **or** a non-empty path marks an explicit endpoint
  (``nats://host:4222/topic``, ``nats://host/topic``). Otherwise the
  authority segment is the topic.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import ParseResult, urlparse

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.transport_types import (
    CANONICAL_TRANSPORTS as _CANONICAL_TRANSPORTS,
    InterfaceTransport,
    normalize_transport as _normalize_transport,
)

if TYPE_CHECKING:
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.types import AgentCard, AgentInterface

    from agntcy_app_sdk.app_sessions import AppSession
    from agntcy_app_sdk.factory import AgntcyFactory
    from agntcy_app_sdk.semantic.a2a.server.config import A2AServerConfig
    from agntcy_app_sdk.semantic.a2a.server.srpc import A2ASlimRpcServerConfig
    from agntcy_app_sdk.transport.base import BaseTransport

logger = get_logger(__name__)

# Well-known default ports for each transport protocol
_SLIM_DEFAULT_PORT = 46357
_NATS_DEFAULT_PORT = 4222

# Default endpoints when using topic-only URLs
_SLIM_DEFAULT_ENDPOINT = f"http://localhost:{_SLIM_DEFAULT_PORT}"
_NATS_DEFAULT_ENDPOINT = f"nats://localhost:{_NATS_DEFAULT_PORT}"


# ---------------------------------------------------------------------------
# Interface transport types — re-exported from shared module for backward
# compatibility.  Canonical definitions live in
# ``agntcy_app_sdk.semantic.a2a.transport_types``.
# ---------------------------------------------------------------------------

# InterfaceTransport, _CANONICAL_TRANSPORTS, _TRANSPORT_ALIASES, and
# _normalize_transport are imported at the top of this file from
# ``agntcy_app_sdk.semantic.a2a.transport_types``.
__all__ = [
    "InterfaceTransport",
    "CardBuilder",
    "ServeCardPlan",
    "parse_interface_url",
]


# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------


def _resolve_slim_endpoint() -> str:
    """Return the SLIM endpoint from ``SLIM_ENDPOINT`` or the default."""
    return os.environ.get("SLIM_ENDPOINT", _SLIM_DEFAULT_ENDPOINT)


def _resolve_nats_endpoint() -> str:
    """Return the NATS endpoint from ``NATS_ENDPOINT`` or the default."""
    return os.environ.get("NATS_ENDPOINT", _NATS_DEFAULT_ENDPOINT)


def _has_explicit_endpoint(parsed_url: ParseResult) -> bool:
    """Whether a NATS URL names a host rather than a bare topic.

    A port or a non-empty path means ``nats://host[:port]/topic``.
    SLIM does not use this helper: only a port marks a SLIM endpoint,
    because a path is part of an ``org/ns/name``.

    Examples:
        ``nats://host:4222/topic``  →  port=4222, path="/topic" → True
        ``nats://host/topic``       →  port=None, path="/topic" → True
        ``nats://my_topic``         →  port=None, path=""       → False
    """
    p = parsed_url
    return p.port is not None or bool(p.path and p.path != "/")


def _url_host(host: str) -> str:
    """Bracket an IPv6 literal so it can be followed by ``:port``."""
    return f"[{host}]" if ":" in host else host


def _slim_endpoint_and_name(parsed: ParseResult) -> tuple[str, str]:
    """Split a SLIM URL into ``(endpoint, name)``.

    A port marks the dataplane address (``slim://host:46357/org/ns/agent``).
    Without a port the remainder is the name (``slim://my_topic``,
    ``slim://org/ns/agent``) and the endpoint comes from ``SLIM_ENDPOINT``
    or ``http://localhost:46357``.  A trailing ``/`` is not part of the name.
    An ``https://`` URL keeps that scheme; ``slim://`` has none, so its
    endpoint is ``http://`` until :meth:`CardBuilder.with_slim_tls` rewrites it.

    ``urlparse`` lowercases the host, so the first segment of a port-less
    name is lowercased.
    """
    p = parsed
    if p.port is not None:
        host = _url_host(p.hostname or "localhost")
        scheme = "https" if (p.scheme or "").lower() == "https" else "http"
        endpoint = f"{scheme}://{host}:{p.port}"
        name = p.path.strip("/")
    else:
        endpoint = _resolve_slim_endpoint()
        hostname = p.hostname or ""
        path = p.path.strip("/")
        name = f"{hostname}/{path}" if path else hostname
    return endpoint, name


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
    transport_type = _normalize_transport(interface.protocol_binding)
    url = interface.url
    parsed = urlparse(url)

    if transport_type == "slimrpc":
        return _parse_slimrpc(url, parsed)

    if transport_type == "slimpatterns":
        return _parse_slim_patterns(url, parsed)

    if transport_type == "natspatterns":
        return _parse_nats_patterns(url, parsed)

    if transport_type in ("jsonrpc", "http"):
        host = parsed.hostname
        port = parsed.port
        if not host or port is None:
            raise ValueError(
                f"JSONRPC/HTTP URL must include an explicit host and port "
                f"(e.g. 'http://0.0.0.0:9999'), got: {url!r}"
            )
        return {"host": host, "port": port}

    raise ValueError(f"Unknown transport type: {transport_type!r}")


def _parse_slimrpc(url: str, parsed: ParseResult) -> dict[str, str | int]:
    """Parse a slimrpc URL.

    Accepted formats::

        slim://org/ns/agent_name            → topic-only (identity = org/ns/agent_name)
        slim://host:46357/org/ns/agent_name → explicit endpoint
    """
    endpoint, identity = _slim_endpoint_and_name(parsed)

    if not identity:
        raise ValueError(f"slimrpc URL must include an identity path: {url!r}")

    return {"endpoint": endpoint, "identity": identity}


def _parse_slim_patterns(url: str, parsed: ParseResult) -> dict[str, str | int]:
    """Parse a slim/slimpatterns URL.

    Accepted formats::

        slim://my_topic                → topic-only
        slim://org/ns/agent            → topic-only (topic = org/ns/agent)
        slim://host:46357/my_topic     → explicit endpoint
        slim://host:46357/org/ns/agent → explicit endpoint
    """
    endpoint, topic = _slim_endpoint_and_name(parsed)

    if not topic:
        raise ValueError(f"slim URL must include a topic: {url!r}")

    return {"endpoint": endpoint, "topic": topic}


def _parse_nats_patterns(url: str, parsed: ParseResult) -> dict[str, str | int]:
    """Parse a nats/natspatterns URL.

    Accepted formats::

        nats://my_topic            → topic-only
        nats://host:4222/my_topic  → explicit endpoint
    """
    p = parsed

    if _has_explicit_endpoint(p):
        # Explicit: nats://host[:port]/topic
        host = _url_host(p.hostname or "localhost")
        port = p.port or _NATS_DEFAULT_PORT
        endpoint = f"nats://{host}:{port}"
        topic = p.path.strip("/")
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
    """Describes what :meth:`CardBuilder.start` *would* start (dry-run output).

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
# CardBuilder — fluent API for expanding an AgentCard into containers
# ---------------------------------------------------------------------------


class CardBuilder:
    """Fluent builder that expands an AgentCard's interfaces into containers.

    Constructed via :meth:`AppSession.add_a2a_card`.  The builder reads
    ``agent_card.supported_interfaces``, creates transport/config objects
    for each, and registers ``AppContainer`` instances on the session.

    Use :meth:`override` to supply a pre-built config or transport for a
    specific interface type, or :meth:`skip` to exclude one entirely.  The
    ``JSONRPC`` interface URL is the address the server binds to; use
    :meth:`with_public_url` to advertise a different, reachable one.
    """

    def __init__(
        self,
        session: AppSession,
        agent_card: AgentCard,
        request_handler: DefaultRequestHandler,
    ):
        self._session = session
        self._agent_card = agent_card
        self._request_handler = request_handler
        self._factory: AgntcyFactory | None = None
        # canonical transport_type -> pre-built
        self._overrides: dict[
            str, BaseTransport | A2ASlimRpcServerConfig | A2AServerConfig
        ] = {}
        self._skips: set[str] = set()  # canonical transport_types to skip
        self._shared_secret: str | None = None
        self._public_url: str | None = None
        self._slim_tls = False

    # -- Fluent setters -----------------------------------------------------

    def with_factory(self, factory: AgntcyFactory) -> CardBuilder:
        """Use an existing :class:`AgntcyFactory` instead of auto-creating one."""
        self._factory = factory
        return self

    def override(
        self,
        transport_type: str,
        target: BaseTransport | A2ASlimRpcServerConfig | A2AServerConfig,
    ) -> CardBuilder:
        """Provide a pre-built config or transport for a specific interface type.

        For ``slimrpc``: pass a pre-built ``A2ASlimRpcServerConfig``.
        For ``slimpatterns`` / ``natspatterns``: pass a pre-built ``BaseTransport``.
        For ``jsonrpc`` / ``http``: pass a pre-built ``A2AServerConfig``.
        """
        self._overrides[_normalize_transport(transport_type)] = target
        return self

    def _transport_override(self, transport_type: str) -> BaseTransport | None:
        from agntcy_app_sdk.transport.base import BaseTransport

        override = self._overrides.get(transport_type)
        if override is not None and not isinstance(override, BaseTransport):
            raise TypeError(
                f".override({transport_type!r}) needs a BaseTransport, "
                f"got {type(override).__name__}"
            )
        return override

    def skip(self, transport_type: str) -> CardBuilder:
        """Exclude an interface type from being started."""
        self._skips.add(_normalize_transport(transport_type))
        return self

    def with_shared_secret(self, secret: str) -> CardBuilder:
        """Provide the SLIM shared secret directly.

        If not called, the builder falls back to the ``SLIM_SHARED_SECRET``
        environment variable.  A :class:`ValueError` is raised at start
        time if neither is available and a SLIM-based transport is declared.
        """
        self._shared_secret = secret
        return self

    def with_slim_tls(self) -> CardBuilder:
        """Connect the ``slim`` and ``slimrpc`` interfaces over TLS.

        A ``slim://`` URL cannot say whether the dataplane speaks TLS.  A
        connection uses TLS when the resolved endpoint is ``https://``: an
        interface URL that is already ``https://host:port/...``, or a
        topic-only URL whose ``SLIM_ENDPOINT`` is ``https://``.  With this
        option every SLIM connection uses TLS (``tls_insecure=False``):
        explicit ``slim://host:46357/...`` endpoints are dialled as
        ``https://host:46357``, and an ``http://`` ``SLIM_ENDPOINT`` is
        refused with a :class:`ValueError` by :meth:`dry_run` and
        :meth:`start` instead of connecting in plaintext.

        The dataplane must present a certificate this host trusts.
        ``.override()`` configs are used as given: this option does not
        rewrite them and does not reject them.
        """
        self._slim_tls = True
        return self

    def with_public_url(self, url: str) -> CardBuilder:
        """Advertise *url* for the HTTP JSON-RPC interface instead of its bind URL.

        In a card, the ``JSONRPC`` interface URL is the address the server
        **binds** to (``http://0.0.0.0:9999``).  That is rarely an address
        clients can reach (``0.0.0.0``, an in-container address, a host behind
        a proxy), yet it is also what the served card advertises.  Set the
        reachable address here: the server still binds to the card's URL, but
        the served card carries *url* for that interface.

        :meth:`ContainerBuilder.with_public_url` on the manual builder appends
        a ``JSONRPC`` interface when the card has none, and leaves an
        interface the author already declared untouched.  On this builder the
        card URL is the bind address, so the served copy's HTTP interface URL
        is replaced with *url*.

        The caller's card object is not modified; a copy with the replaced URL
        is served.  The option replaces the URL of the one HTTP interface that
        is served; it is ignored with a warning when there is none (no such
        interface, ``.skip("jsonrpc")``, or an ``.override("jsonrpc", ...)``
        config, which is served as given), and rejected when several HTTP
        interfaces would be served, since one URL cannot describe all of them.

        An ``extended_agent_card`` on the request handler is served as given;
        set its URLs yourself.
        """
        if not url.startswith(("http://", "https://")):
            raise ValueError(
                f"public URL must start with http:// or https://, got {url!r}"
            )
        self._public_url = url
        return self

    # -- Helpers ------------------------------------------------------------

    def _slim_connection(
        self, interface: AgentInterface, endpoint: str
    ) -> tuple[str, bool]:
        """Return the ``(endpoint, tls_insecure)`` for a SLIM interface."""
        if self._slim_tls and urlparse(interface.url).port is not None:
            endpoint = "https://" + endpoint.split("://", 1)[1]
        is_https = endpoint.lower().startswith("https://")
        if self._slim_tls and not is_https:
            raise ValueError(
                f"with_slim_tls() requires an https:// SLIM endpoint, but "
                f"{interface.url!r} resolves to {endpoint!r} (from SLIM_ENDPOINT "
                "or the default). Set SLIM_ENDPOINT to an https:// URL."
            )
        return endpoint, not is_https

    def _served_card(
        self, interfaces: list[AgentInterface]
    ) -> tuple[AgentCard, int | None]:
        """Return the card to serve and the index of the interface whose URL
        was replaced by :meth:`with_public_url` (``None`` if untouched).

        Without a public URL this is the caller's card, unchanged.
        """
        if self._public_url is None:
            return self._agent_card, None

        http_indexes: list[int] = []
        for i, interface in enumerate(interfaces):
            transport_type = _normalize_transport(interface.protocol_binding)
            if transport_type in ("jsonrpc", "http") and (
                transport_type not in self._skips
            ):
                http_indexes.append(i)

        if not http_indexes:
            logger.warning(
                "with_public_url(%r) is ignored: no HTTP JSON-RPC interface "
                "is served (none declared on the card, or it was skipped).",
                self._public_url,
            )
            return self._agent_card, None

        if len(http_indexes) > 1:
            raise ValueError(
                "with_public_url() needs exactly one served HTTP JSON-RPC "
                f"interface, but the card has {len(http_indexes)}. One URL "
                "cannot describe all of them; .skip() the extra ones."
            )

        index = http_indexes[0]
        transport_type = _normalize_transport(interfaces[index].protocol_binding)
        if self._overrides.get(transport_type) is not None:
            logger.warning(
                "with_public_url(%r) is ignored: the %r interface is served "
                "from an .override() config, which is used as given.",
                self._public_url,
                transport_type,
            )
            return self._agent_card, None

        from agntcy_app_sdk.semantic.a2a.card_utils import clone_card

        served = clone_card(self._agent_card)
        served.supported_interfaces[index].url = self._public_url
        return served, index

    def _warn_on_slim_name_collision(self, interfaces: list[AgentInterface]) -> None:
        """Warn when ``slim`` (pub/sub) and ``slimrpc`` share one SLIM name.

        SLIM 2.x hands an incoming session to a single subscriber of a name.
        If a pub/sub app and an RPC server subscribe under the same name, RPC
        calls can be delivered to the pub/sub app (which has no RPC handler)
        and hang until they time out.
        """
        rpc_names: set[str] = set()
        pubsub_names: set[str] = set()
        for interface in interfaces:
            transport_type = _normalize_transport(interface.protocol_binding)
            if transport_type in self._skips:
                continue
            if transport_type == "slimrpc":
                rpc_names.add(str(parse_interface_url(interface)["identity"]))
            elif transport_type == "slimpatterns":
                pubsub_names.add(str(parse_interface_url(interface)["topic"]))

        for name in sorted(rpc_names & pubsub_names):
            logger.warning(
                "The card declares both a 'slim' and a 'slimrpc' interface "
                "for '%s'. SLIM 2.x delivers a session to only one subscriber "
                "of a name, so slimrpc calls may never be answered. Give the "
                "two interfaces different identities, or .skip() one of them.",
                name,
            )

    # -- Terminal operations ------------------------------------------------

    async def dry_run(self) -> ServeCardPlan:
        """Return a plan describing what would be started."""
        result = await self._execute(dry_run=True, keep_alive=False)
        assert isinstance(result, ServeCardPlan)
        return result

    async def start(self, *, keep_alive: bool = False) -> None:
        """Build all containers and start sessions."""
        await self._execute(dry_run=False, keep_alive=keep_alive)

    # -- Core logic ---------------------------------------------------------

    async def _execute(
        self, *, dry_run: bool, keep_alive: bool
    ) -> ServeCardPlan | None:
        """Internal execution engine (shared by :meth:`start` and :meth:`dry_run`)."""
        from agntcy_app_sdk.factory import AgntcyFactory
        from agntcy_app_sdk.semantic.a2a.server.config import A2AServerConfig
        from agntcy_app_sdk.semantic.a2a.server.srpc import (
            A2ASlimRpcServerConfig,
            SlimRpcConnectionConfig,
        )

        session = self._session
        agent_card = self._agent_card
        request_handler = self._request_handler

        if self._factory is None:
            self._factory = AgntcyFactory()
        factory = self._factory

        interfaces = list(agent_card.supported_interfaces)
        if not interfaces:
            raise ValueError(
                "agent_card.supported_interfaces is empty; nothing to serve"
            )

        plan = ServeCardPlan()

        # The interface URLs above say where to *bind*; the card that is
        # served may advertise a different, reachable URL (with_public_url).
        served_card, public_index = self._served_card(interfaces)

        # Build the A2AServerConfig once for transports that need it
        # (slim/nats patterns and jsonrpc all share the same config instance).
        a2a_app = A2AServerConfig(
            agent_card=served_card, request_handler=request_handler
        )

        self._warn_on_slim_name_collision(interfaces)

        for i, interface in enumerate(interfaces):
            transport_type = _normalize_transport(interface.protocol_binding)

            if transport_type not in _CANONICAL_TRANSPORTS:
                logger.warning(
                    "Unknown transport type '%s' on interface %d, skipping",
                    interface.protocol_binding,
                    i,
                )
                continue

            # Skip check
            if transport_type in self._skips:
                logger.debug(
                    "Skipping interface %d (transport '%s' in skip list)",
                    i,
                    transport_type,
                )
                continue

            parsed = parse_interface_url(interface)
            # An override is the caller's transport, so the card URL's TLS
            # decision does not apply to it and must not reject the start.
            slim_override = (
                self._overrides.get(transport_type)
                if transport_type in ("slimrpc", "slimpatterns")
                else None
            )
            if transport_type in ("slimrpc", "slimpatterns") and slim_override is None:
                slim_endpoint, tls_insecure = self._slim_connection(
                    interface, str(parsed["endpoint"])
                )
                parsed["endpoint"] = slim_endpoint
            else:
                tls_insecure = True

            if transport_type == "slimrpc":
                override = self._overrides.get("slimrpc")
                session_id = f"slimrpc-{i}"

                if override is None:
                    shared_secret = self._shared_secret or os.environ.get(
                        "SLIM_SHARED_SECRET"
                    )
                    if not shared_secret:
                        raise ValueError(
                            "SLIM shared secret required for slimrpc interface. "
                            "Use .with_shared_secret() or set SLIM_SHARED_SECRET "
                            "env var."
                        )

                log = logger.bind(transport=transport_type, session_id=session_id)
                log.info("Serving interface", **parsed)

                if dry_run:
                    detail = (
                        f"endpoint={parsed['endpoint']}, identity={parsed['identity']}"
                    )
                    if override is None and not tls_insecure:
                        detail += ", tls=on"
                    if override is not None:
                        detail += " (source: override)"
                    plan.containers.append(
                        {
                            "session_id": session_id,
                            "transport": "slimrpc",
                            "detail": detail,
                        }
                    )
                    continue

                if override is not None:
                    session.add(override).with_session_id(session_id).build()
                else:
                    assert shared_secret is not None
                    config = A2ASlimRpcServerConfig(
                        agent_card=served_card,
                        request_handler=request_handler,
                        connection=SlimRpcConnectionConfig(
                            identity=str(parsed["identity"]),
                            shared_secret=shared_secret,
                            endpoint=str(parsed["endpoint"]),
                            tls_insecure=tls_insecure,
                        ),
                    )
                    session.add(config).with_session_id(session_id).build()

            elif transport_type == "slimpatterns":
                override = self._transport_override("slimpatterns")
                session_id = f"slim-{i}"
                # Use the topic from the interface URL as the SLIM routable
                # name — in SLIM, the transport name IS the routing address
                # that clients send to.
                routable_name = str(parsed["topic"])

                if override is None:
                    shared_secret = self._shared_secret or os.environ.get(
                        "SLIM_SHARED_SECRET"
                    )
                    if not shared_secret:
                        raise ValueError(
                            "SLIM shared secret required for slim interface. "
                            "Use .with_shared_secret() or set SLIM_SHARED_SECRET "
                            "env var."
                        )

                log = logger.bind(transport=transport_type, session_id=session_id)
                log.info("Serving interface", **parsed)

                if dry_run:
                    detail = (
                        f"endpoint={parsed['endpoint']}, "
                        f"topic={parsed['topic']}, "
                        f"name={routable_name}"
                    )
                    if override is None and not tls_insecure:
                        detail += ", tls=on"
                    if override is not None:
                        detail += " (source: override)"
                    plan.containers.append(
                        {
                            "session_id": session_id,
                            "transport": transport_type,
                            "detail": detail,
                        }
                    )
                    continue

                if override is not None:
                    transport = override
                else:
                    transport = factory.create_transport(
                        "SLIM",
                        endpoint=str(parsed["endpoint"]),
                        name=routable_name,
                        shared_secret_identity=shared_secret,
                        tls_insecure=tls_insecure,
                    )
                (
                    session.add(a2a_app)
                    .with_transport(transport)
                    .with_topic(str(parsed["topic"]))
                    .with_session_id(session_id)
                    .build()
                )

            elif transport_type == "natspatterns":
                override = self._transport_override("natspatterns")
                session_id = f"nats-{i}"

                log = logger.bind(transport=transport_type, session_id=session_id)
                log.info("Serving interface", **parsed)

                if dry_run:
                    detail = f"endpoint={parsed['endpoint']}, topic={parsed['topic']}"
                    if override is not None:
                        detail += " (source: override)"
                    plan.containers.append(
                        {
                            "session_id": session_id,
                            "transport": transport_type,
                            "detail": detail,
                        }
                    )
                    continue

                if override is not None:
                    transport = override
                else:
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
                override = self._overrides.get(transport_type)
                session_id = f"http-{i}"

                log = logger.bind(transport=transport_type, session_id=session_id)
                log.info("Serving interface", **parsed)

                if dry_run:
                    detail = f"host={parsed['host']}, port={parsed['port']}"
                    if public_index == i:
                        detail += f", advertised={self._public_url}"
                    if override is not None:
                        detail += " (source: override)"
                    plan.containers.append(
                        {
                            "session_id": session_id,
                            "transport": transport_type,
                            "detail": detail,
                        }
                    )
                    continue

                app_target = override if override is not None else a2a_app
                (
                    session.add(app_target)
                    .with_host(str(parsed["host"]))
                    .with_port(int(parsed["port"]))
                    .with_session_id(session_id)
                    .build()
                )

        if dry_run:
            return plan

        logger.debug(
            "Starting all sessions",
            interfaces=len(interfaces),
            keep_alive=keep_alive,
        )
        await session.start_all_sessions(keep_alive=keep_alive)
        return None
