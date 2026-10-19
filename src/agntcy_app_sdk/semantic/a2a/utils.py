# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A2A utility functions for identifier extraction from AgentCard metadata."""

from __future__ import annotations

from urllib.parse import urlparse

from a2a.types import AgentCard

from agntcy_app_sdk.semantic.a2a.client.transports import _PATTERNS_SCHEMES
from agntcy_app_sdk.semantic.a2a.transport_types import (
    InterfaceTransport,
    name_host,
    normalize_transport,
)

# card_bootstrap also accepts http(s):// URLs for SLIM interfaces
# (``https://host:46357/org/ns/agent`` selects TLS).
_SLIM_BINDINGS = frozenset(
    {InterfaceTransport.SLIM_RPC, InterfaceTransport.SLIM_PATTERNS}
)
_SLIM_HTTP_SCHEMES = frozenset({"http", "https"})


def get_agent_identifier(
    agent_card: AgentCard,
    interface_type: str | None = None,
) -> str | None:
    """Extract the transport identifier from an agent card's interface metadata.

    Looks up the card's ``supported_interfaces`` to find a matching
    transport entry and extracts the identifier portion of its URI (the
    part after the ``scheme://``) — typically a topic for patterns
    transports or an identity for SLIM-RPC.

    Resolution order:

    1. If ``interface_type`` is provided (e.g.
       ``InterfaceTransport.SLIM_PATTERNS``), search
       ``supported_interfaces`` for an entry whose ``protocol_binding``
       matches (case-insensitive) and extract the identifier from its URL.
    2. If ``interface_type`` is *not* provided, walk ``supported_interfaces``
       in card order (the server's preference) and return the identifier of
       the first interface that has one.  Interfaces without an identifier,
       such as ``JSONRPC`` over ``http://``, are skipped.
    3. Return ``None`` if no matching interface or parseable identifier
       is found.

    A ``slimrpc`` interface may declare its identity as a bare
    ``org/ns/name`` (what the SLIM-RPC server handler writes), a
    ``slim://`` URL, or an ``http(s)://host:port/org/ns/name`` URL; all
    yield ``org/ns/name``.

    Args:
        agent_card: The agent card to inspect.
        interface_type: Optional transport identifier to match (e.g.
            ``"slimpatterns"``, ``"natspatterns"``).  Accepts any value
            from :class:`~agntcy_app_sdk.semantic.a2a.server.card_bootstrap.InterfaceTransport`.

    Returns:
        The extracted identifier string, or ``None`` if no match is found.

    Examples::

        from agntcy_app_sdk.semantic.a2a.utils import get_agent_identifier
        from agntcy_app_sdk import InterfaceTransport

        # Explicit interface type
        topic = get_agent_identifier(card, InterfaceTransport.SLIM_PATTERNS)
        # → "my_topic"  (from the matching supported_interfaces entry)

        # Auto-detect: first interface, in card order, that has an identifier
        topic = get_agent_identifier(card)
        # → "Weather_Agent_1.0.0"  (from the first supported_interfaces URL)
    """
    if interface_type is not None:
        return _find_topic_by_interface(agent_card, interface_type)

    for iface in agent_card.supported_interfaces:
        topic = _extract_topic(iface.url, iface.protocol_binding)
        if topic is not None:
            return topic

    return None


def _find_topic_by_interface(agent_card: AgentCard, interface_type: str) -> str | None:
    """Search ``supported_interfaces`` for a matching transport and extract."""
    needle = normalize_transport(interface_type)
    for iface in agent_card.supported_interfaces:
        if normalize_transport(iface.protocol_binding) == needle:
            topic = _extract_topic(iface.url, iface.protocol_binding)
            if topic is not None:
                return topic

    return None


def _extract_topic(url: str, protocol_binding: str) -> str | None:
    """Extract the topic (or SLIM-RPC identity) from an interface URL.

    Handles both topic-only and explicit-endpoint formats::

        "slim://my_topic"                 →  "my_topic"
        "slim://localhost:46357/my_topic" →  "my_topic"
        "nats://my_topic"                 →  "my_topic"
        "nats://localhost:4222/my_topic"  →  "my_topic"
        "slim://default/default/agent"    →  "default/default/agent"
        "https://host:46357/org/ns/agent" →  "org/ns/agent"
                                             (SLIM bindings only)
        "default/default/agent"           →  "default/default/agent"
                                             (``slimrpc`` binding only)

    A trailing ``/`` is not part of the topic.  Returns ``None`` for other
    schemes (e.g. a ``JSONRPC`` ``http://`` URL) or when no topic can be
    determined.
    """
    binding = normalize_transport(protocol_binding)
    if "://" not in url:
        if binding == InterfaceTransport.SLIM_RPC:
            return url.strip("/") or None
        return None
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    slim_http = binding in _SLIM_BINDINGS and scheme in _SLIM_HTTP_SCHEMES
    if scheme not in _PATTERNS_SCHEMES and not slim_http:
        return None
    path = parsed.path.strip("/")
    # Explicit endpoint: has a port → topic is the path
    if parsed.port is not None:
        return path or None
    # Topic-only: hostname (+ path if slashes present) IS the topic
    hostname = name_host(parsed)
    result = f"{hostname}/{path}" if path else hostname
    return result or None
