# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Shared transport type constants and alias resolution for A2A interfaces.

This module is the single source of truth for transport identifiers used
across both server (card_bootstrap) and client (factory) code.  Import
:func:`normalize_transport` wherever a raw transport string from an
``AgentCard`` or ``AgentInterface`` needs to be resolved to its canonical
form before matching or dispatch.
"""

from __future__ import annotations


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
        return set(TRANSPORT_ALIASES.keys()) | CANONICAL_TRANSPORTS

    @classmethod
    def canonical_types(cls) -> set[str]:
        """Return only the canonical (non-alias) transport types."""
        return set(CANONICAL_TRANSPORTS)


# Canonical transports (used for dispatch after normalisation)
CANONICAL_TRANSPORTS: frozenset[str] = frozenset(
    {
        "slimrpc",
        "slimpatterns",
        "natspatterns",
        "jsonrpc",
        "http",
    }
)

# Alias → canonical mapping (lower-case keys)
TRANSPORT_ALIASES: dict[str, str] = {
    "slim": "slimpatterns",
    "slim-extended": "slimpatterns",
    "nats": "natspatterns",
}


def normalize_transport(raw: str) -> str:
    """Normalise a transport identifier to its canonical form.

    Applies case-folding and alias resolution.  Returns the canonical
    string or the lower-cased input if it is already canonical.
    """
    key = raw.lower()
    return TRANSPORT_ALIASES.get(key, key)
