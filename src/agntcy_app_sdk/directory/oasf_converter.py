# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Pure-function converters between A2A AgentCard and OASF record dicts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from a2a.types import AgentCard

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

    The entire card is stored verbatim inside an OASF
    ``modules[].data.card_data`` field so it can be round-tripped back to an
    ``AgentCard`` without loss.
    """
    card_dict = card.model_dump(mode="json", exclude_none=True)

    # Extract metadata from the card for top-level OASF fields.
    authors: list[str] = []
    if card.provider and card.provider.organization:
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

    Returns ``None`` when no matching module is present.
    """
    modules = oasf_data.get("modules", [])
    for module in modules:
        if module.get("name") == MODULE_NAME_A2A:
            card_data = module.get("data", {}).get("card_data")
            if card_data is not None:
                return AgentCard.model_validate(card_data)
    return None
