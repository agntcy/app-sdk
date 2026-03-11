# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""A2A utility functions for identifier extraction from AgentCard metadata."""

from __future__ import annotations

from urllib.parse import urlparse

from a2a.types import AgentCard

from agntcy_app_sdk.semantic.a2a.client.transports import _PATTERNS_SCHEMES
from agntcy_app_sdk.semantic.a2a.transport_types import normalize_transport


def get_agent_identifier(
    agent_card: AgentCard,
    interface_type: str | None = None,
) -> str | None:
    """Extract the transport identifier from an agent card's interface metadata.

    Looks up the card's ``additional_interfaces`` and ``url`` to find
    a matching transport entry and extracts the identifier portion of
    its URI (the part after the ``scheme://``) — typically a topic for
    patterns transports or an identity for SLIM-RPC.

    Resolution order:

    1. If ``interface_type`` is provided (e.g.
       ``InterfaceTransport.SLIM_PATTERNS``), search
       ``additional_interfaces`` for an entry whose ``transport``
       matches (case-insensitive) and extract the identifier from its URL.
    2. If ``interface_type`` is *not* provided, use
       ``card.preferred_transport`` to match against
       ``additional_interfaces``, then fall back to ``card.url``.
    3. Return ``None`` if no matching interface or parseable identifier
       is found.

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
        # → "my_topic"  (from additional_interfaces entry)

        # Auto-detect from preferred_transport
        topic = get_agent_identifier(card)
        # → "Weather_Agent_1.0.0"  (from card.url matching preferred_transport)
    """
    if interface_type is not None:
        return _find_topic_by_interface(agent_card, interface_type)

    # No explicit type — try preferred_transport
    preferred = agent_card.preferred_transport
    if preferred:
        topic = _find_topic_by_interface(agent_card, preferred)
        if topic is not None:
            return topic

        # Fall back to card.url if its scheme matches preferred_transport
        if agent_card.url:
            topic = _extract_topic(agent_card.url)
            if topic is not None:
                return topic

    return None


def _find_topic_by_interface(agent_card: AgentCard, interface_type: str) -> str | None:
    """Search ``additional_interfaces`` for a matching transport and extract."""
    if not agent_card.additional_interfaces:
        return None

    needle = normalize_transport(interface_type)
    for iface in agent_card.additional_interfaces:
        if normalize_transport(iface.transport) == needle:
            topic = _extract_topic(iface.url)
            if topic is not None:
                return topic

    return None


def _extract_topic(url: str) -> str | None:
    """Extract the topic from a patterns-scheme URI.

    Handles both topic-only and explicit-endpoint formats::

        "slim://my_topic"                 →  "my_topic"
        "slim://localhost:46357/my_topic" →  "my_topic"
        "nats://my_topic"                 →  "my_topic"
        "nats://localhost:4222/my_topic"  →  "my_topic"
        "slim://default/default/agent"    →  "default/default/agent"

    Returns ``None`` for non-patterns schemes (http, etc.) or when
    no topic can be determined.
    """
    if "://" not in url:
        return None
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _PATTERNS_SCHEMES:
        return None
    # Explicit endpoint: has a port → topic is the path
    if parsed.port is not None:
        path = parsed.path.lstrip("/")
        return path or None
    # Topic-only: hostname (+ path if slashes present) IS the topic
    hostname = parsed.hostname or ""
    path = parsed.path.lstrip("/")
    result = f"{hostname}/{path}" if path else hostname
    return result or None
