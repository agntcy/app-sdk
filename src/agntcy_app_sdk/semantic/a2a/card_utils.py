# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Helpers for reading and writing transports on an A2A ``AgentCard``.

a2a-sdk 1.x replaced the ``AgentCard.url`` / ``preferred_transport`` /
``additional_interfaces`` trio with a single ordered list,
``AgentCard.supported_interfaces``.  Each entry is an
``AgentInterface(protocol_binding, url, protocol_version, tenant)``.  The
**order** of the list expresses the server's preference — the first entry is
the "preferred transport".

The helpers in this module give the rest of the SDK a small, transport-name
aware API over that list.  Transport names are compared after
:func:`~agntcy_app_sdk.semantic.a2a.transport_types.normalize_transport`
(case-folding and alias resolution), so ``"jsonrpc"``, ``"JSONRPC"`` and
``"slim"`` / ``"slimpatterns"`` all match as expected.
"""

from __future__ import annotations

from a2a.types import (
    AgentCard,
    AgentInterface,
    HTTPAuthSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)

from agntcy_app_sdk.common.logging_config import get_logger
from agntcy_app_sdk.semantic.a2a.transport_types import normalize_transport

logger = get_logger(__name__)

IDENTITY_AUTH_SCHEME = "IdentityServiceAuthScheme"
"""Key of the Identity Service bearer-JWT scheme in ``security_schemes``."""

# Canonical transport -> casing expected by the upstream a2a-sdk
# (``a2a.utils.constants.TransportProtocol``).  SDK-only transports
# (slimrpc, slimpatterns, natspatterns) are kept lower-case.
_UPSTREAM_BINDING_CASING: dict[str, str] = {
    "jsonrpc": "JSONRPC",
    "grpc": "GRPC",
    "http+json": "HTTP+JSON",
}

__all__ = [
    "wire_binding",
    "find_interface",
    "preferred_transport",
    "add_interface",
    "clone_card",
    "get_card_url",
    "declare_identity_auth",
]


def wire_binding(transport: str) -> str:
    """Return the ``protocol_binding`` string to put on the wire.

    Known upstream bindings are returned in the casing the upstream
    ``ClientFactory`` expects (``"JSONRPC"``); SDK-specific transports are
    returned in their canonical lower-case form (``"slimpatterns"``).
    """
    key = normalize_transport(transport)
    return _UPSTREAM_BINDING_CASING.get(key, key)


def _find_index(card: AgentCard, transport: str) -> int | None:
    needle = normalize_transport(transport)
    for idx, iface in enumerate(card.supported_interfaces):
        if normalize_transport(iface.protocol_binding) == needle:
            return idx
    return None


def find_interface(card: AgentCard, transport: str) -> AgentInterface | None:
    """Return the first interface on *card* matching *transport*, if any."""
    idx = _find_index(card, transport)
    return card.supported_interfaces[idx] if idx is not None else None


def preferred_transport(card: AgentCard) -> str | None:
    """Return the ``protocol_binding`` of the first (preferred) interface."""
    if card.supported_interfaces:
        return card.supported_interfaces[0].protocol_binding or None
    return None


def get_card_url(card: AgentCard) -> str | None:
    """Return the URL of the first (preferred) interface.

    Replacement for the removed ``AgentCard.url`` field.
    """
    if card.supported_interfaces:
        return card.supported_interfaces[0].url or None
    return None


def clone_card(card: AgentCard) -> AgentCard:
    """Return an independent deep copy of *card*."""
    duplicate = AgentCard()
    duplicate.CopyFrom(card)
    return duplicate


def _copy_interface(iface: AgentInterface) -> AgentInterface:
    duplicate = AgentInterface()
    duplicate.CopyFrom(iface)
    return duplicate


def _replace_interfaces(card: AgentCard, interfaces: list[AgentInterface]) -> None:
    """Rewrite ``card.supported_interfaces`` with *interfaces* (order kept)."""
    rebuilt = [_copy_interface(i) for i in interfaces]
    del card.supported_interfaces[:]
    card.supported_interfaces.extend(rebuilt)


def add_interface(
    card: AgentCard,
    transport: str,
    url: str,
    *,
    prefer: bool = False,
    replace: bool = False,
    protocol_version: str = "",
) -> AgentInterface:
    """Ensure *card* declares an interface for *transport*.

    Args:
        card: The card to mutate in place.
        transport: Transport name (aliases and any casing accepted).
        url: URL to declare for the interface.
        prefer: Move the interface to the front of the list so it becomes the
            card's preferred transport.
        replace: When an interface for *transport* already exists, overwrite
            its URL.  By default an existing entry is left untouched because
            it was declared explicitly by the agent author.
        protocol_version: Optional ``protocol_version`` for a newly-created
            interface.

    Returns:
        The (new or existing) ``AgentInterface`` now present on the card.
    """
    idx = _find_index(card, transport)

    if idx is not None:
        if replace:
            card.supported_interfaces[idx].url = url
        if prefer and idx != 0:
            interfaces = list(card.supported_interfaces)
            interfaces.insert(0, interfaces.pop(idx))
            _replace_interfaces(card, interfaces)
    else:
        new_iface = AgentInterface(
            protocol_binding=wire_binding(transport),
            url=url,
            protocol_version=protocol_version,
        )
        interfaces = list(card.supported_interfaces)
        if prefer:
            interfaces.insert(0, new_iface)
        else:
            interfaces.append(new_iface)
        _replace_interfaces(card, interfaces)

    result = find_interface(card, transport)
    assert result is not None
    return result


def declare_identity_auth(card: AgentCard) -> None:
    """Make Identity Service bearer-JWT auth the card's only security requirement.

    Edits *card* in place, like the interfaces the server handlers declare:
    every handler serving one card shares it.  The HTTP JSON-RPC, SLIM /
    NATS patterns and ``slimrpc`` handlers enforce this scheme.

    Requirements already on the card are replaced, with a warning, because
    the server enforces only this scheme.  The schemes they referenced are
    removed too, so the card does not define checks nobody performs, and so
    are skill requirements that name a removed scheme.  Schemes no card-level
    requirement named, and skill requirements that name only such schemes,
    stay on the card; no handler enforces them.  Editing a card invalidates
    any ``signatures`` on it.
    """
    requirement = SecurityRequirement(
        schemes={IDENTITY_AUTH_SCHEME: StringList(list=["*"])}
    )
    dropped = [r for r in card.security_requirements if r != requirement]
    dropped_schemes = {s for r in dropped for s in r.schemes} - {IDENTITY_AUTH_SCHEME}
    if dropped:
        logger.warning(
            "Identity auth replaces the security requirements declared on "
            "agent card %r (%s): the server enforces only %s.",
            card.name,
            ", ".join(sorted(dropped_schemes))
            or "a differently scoped identity requirement",
            IDENTITY_AUTH_SCHEME,
        )
    for skill in card.skills:
        kept = [
            r
            for r in skill.security_requirements
            if not set(r.schemes) & dropped_schemes
        ]
        if len(kept) != len(skill.security_requirements):
            logger.warning(
                "Skill %r of agent card %r: dropped requirements on removed "
                "schemes (%s)",
                skill.id,
                card.name,
                ", ".join(sorted(dropped_schemes)),
            )
            del skill.security_requirements[:]
            skill.security_requirements.extend(kept)
    for name in dropped_schemes:
        card.security_schemes.pop(name, None)
    card.security_schemes[IDENTITY_AUTH_SCHEME].CopyFrom(
        SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(
                scheme="bearer",
                bearer_format="JWT",
            )
        )
    )
    del card.security_requirements[:]
    card.security_requirements.append(requirement)
