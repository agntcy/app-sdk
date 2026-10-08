# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Pure-function converters between A2A AgentCard and OASF record dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from a2a.types import AgentCard
from google.protobuf.json_format import MessageToDict, ParseDict

MODULE_NAME_A2A = "integration/a2a"
CARD_SCHEMA_VERSION = "v1.0.0"
OASF_SCHEMA_VERSION = "1.0.0"

# OASF class IDs (category_uid * 100 + uid within category)
# See https://github.com/agntcy/oasf schema/module_categories.json and
# schema/modules/integration/a2a.json
MODULE_ID_A2A = 203  # integration (2) + a2a (3)
# Default skill used as a placeholder when the card has no skills.
# 101 = NLP category (1) + text generation (01)
DEFAULT_SKILL_ID = 101


def agent_card_to_oasf(card: AgentCard) -> dict[str, Any]:
    """Convert an A2A ``AgentCard`` to an OASF record dict.

    The entire card is stored verbatim (as ProtoJSON, the canonical A2A v1
    JSON form) inside an OASF ``modules[].data.card_data`` field so it can be
    round-tripped back to an ``AgentCard`` without loss.
    """
    card_dict = MessageToDict(card)

    # Extract metadata from the card for top-level OASF fields.
    authors: list[str] = []
    if card.HasField("provider") and card.provider.organization:
        authors.append(card.provider.organization)
    # OASF requires non-empty authors; fall back to the card name.
    if not authors:
        authors.append(card.name)

    return {
        "name": card.name,
        "schema_version": OASF_SCHEMA_VERSION,
        "version": card.version if card.version else "0.0.0",
        "description": card.description if card.description else "",
        "authors": authors,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "skills": [{"id": DEFAULT_SKILL_ID}],
        "domains": [],
        "modules": [
            {
                "id": MODULE_ID_A2A,
                "name": MODULE_NAME_A2A,
                "data": {
                    "card_data": card_dict,
                    "card_schema_version": CARD_SCHEMA_VERSION,
                },
            },
        ],
    }


def oasf_to_agent_card(oasf_data: dict[str, Any]) -> AgentCard | None:
    """Extract an A2A ``AgentCard`` from an OASF record dict.

    Scans the ``modules`` list for an entry whose ``name`` is
    ``integration/a2a`` and, if found, deserializes the embedded
    ``card_data`` back into an ``AgentCard``.

    Records written before the a2a-sdk 1.x migration store the card in the
    legacy v0.3 shape (``url`` / ``preferredTransport`` /
    ``additionalInterfaces``).  Those are detected and converted to the v1
    ``supported_interfaces`` form so old directory entries stay readable.

    Returns ``None`` when no matching module is present.
    """
    modules = oasf_data.get("modules", [])
    for module in modules:
        if module.get("name") == MODULE_NAME_A2A:
            card_data = module.get("data", {}).get("card_data")
            if card_data is not None:
                return _card_from_dict(card_data)
    return None


def _is_legacy_card_dict(card_data: dict[str, Any]) -> bool:
    """Return ``True`` for a pre-1.x (v0.3, Pydantic-JSON) card dict."""
    if "supportedInterfaces" in card_data or "supported_interfaces" in card_data:
        return False
    return any(
        key in card_data
        for key in ("url", "preferredTransport", "additionalInterfaces")
    )


def _card_from_dict(card_data: dict[str, Any]) -> AgentCard:
    """Build an ``AgentCard`` from a stored dict (v1 ProtoJSON or legacy v0.3)."""
    if _is_legacy_card_dict(card_data):
        from a2a.compat.v0_3 import conversions
        from a2a.compat.v0_3 import types as types_v03

        legacy = types_v03.AgentCard.model_validate(card_data)
        return conversions.to_core_agent_card(legacy)

    return ParseDict(card_data, AgentCard(), ignore_unknown_fields=True)
