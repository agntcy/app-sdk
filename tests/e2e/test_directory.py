# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for the AgentDirectory integration with a live directory service.

Requires ``dir-api-server`` and ``zot`` running via docker-compose:

    docker-compose -f services/docker/docker-compose.yaml up -d dir-api-server zot
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from a2a.types import AgentCapabilities, AgentCard, AgentProvider

from agntcy_app_sdk.directory import (
    AgentDirectory,
    agent_card_to_oasf,
    oasf_to_agent_card,
)
from agntcy_app_sdk.directory.oasf_converter import MODULE_NAME_A2A

pytest_plugins = "pytest_asyncio"

DIR_SERVER_ADDRESS = "127.0.0.1:8888"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_card(**overrides) -> AgentCard:
    """Build a valid AgentCard with a unique name for test isolation."""
    unique = uuid.uuid4().hex[:8]
    defaults = {
        "name": f"e2e-test-agent-{unique}",
        "url": "http://localhost:9000",
        "version": "1.0.0",
        "description": "E2E test agent for directory integration",
        "capabilities": AgentCapabilities(),
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [],
        "provider": AgentProvider(
            organization="E2E Test Org", url="https://test.example.com"
        ),
    }
    defaults.update(overrides)
    return AgentCard(**defaults)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def directory():
    """Create an AgentDirectory connected to the local dir-api-server."""
    from agntcy.dir_sdk.client.config import Config

    config = Config(server_address=DIR_SERVER_ADDRESS)
    d = AgentDirectory(config=config)
    await d.setup()
    yield d
    await d.teardown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_and_pull_agent_card(directory: AgentDirectory):
    """Push an AgentCard, pull it back by CID, and verify round-trip fidelity."""
    card = _unique_card()

    # Push
    cid = await directory.push_agent_record(card)
    assert cid, "push_agent_record should return a non-empty CID"
    print(f"\n  Pushed AgentCard '{card.name}' → CID: {cid}")

    # Pull as raw OASF dict
    oasf_dict = await directory.pull_agent_record(cid)
    assert isinstance(oasf_dict, dict), "pull should return a dict by default"
    assert oasf_dict["name"] == card.name

    # Verify the A2A module is present
    modules = oasf_dict.get("modules", [])
    a2a_modules = [m for m in modules if m.get("name") == MODULE_NAME_A2A]
    assert len(a2a_modules) == 1, "Should have exactly one integration/a2a module"

    print(f"  Pulled OASF record — name: {oasf_dict['name']}")


@pytest.mark.asyncio
async def test_push_and_pull_extract_card(directory: AgentDirectory):
    """Push an AgentCard, pull with extract_card=True, and verify AgentCard fields."""
    card = _unique_card()

    cid = await directory.push_agent_record(card)
    print(f"\n  Pushed '{card.name}' → CID: {cid}")

    # Pull and extract the AgentCard
    restored_card = await directory.pull_agent_record(cid, extract_card=True)
    assert isinstance(
        restored_card, AgentCard
    ), "extract_card should return an AgentCard"
    assert restored_card.name == card.name
    assert restored_card.url == card.url
    assert restored_card.version == card.version
    assert restored_card.description == card.description

    print(
        f"  Extracted AgentCard: name={restored_card.name}, version={restored_card.version}"
    )


@pytest.mark.asyncio
async def test_push_raw_oasf_dict(directory: AgentDirectory):
    """Push a raw OASF dict (not an AgentCard) and pull it back."""
    card = _unique_card()
    oasf_dict = agent_card_to_oasf(card)

    cid = await directory.push_agent_record(oasf_dict)
    assert cid, "push should return a CID for raw dicts too"
    print(f"\n  Pushed raw OASF dict '{oasf_dict['name']}' → CID: {cid}")

    pulled = await directory.pull_agent_record(cid)
    assert isinstance(pulled, dict)
    assert pulled["name"] == card.name

    print(f"  Pulled back: {pulled['name']}")


@pytest.mark.asyncio
async def test_search_by_name(directory: AgentDirectory):
    """Push a record and search for it by name."""
    card = _unique_card()

    cid = await directory.push_agent_record(card)
    print(f"\n  Pushed '{card.name}' → CID: {cid}")

    # Search by the unique name
    results = await directory.search_agent_records(card.name, limit=5)
    assert len(results) >= 1, f"Expected at least 1 search result for '{card.name}'"

    # Verify our record is in the results
    names = [r.get("name") for r in results]
    assert card.name in names, f"Expected '{card.name}' in search results, got: {names}"

    print(f"  Search returned {len(results)} result(s) — found '{card.name}'")


@pytest.mark.asyncio
async def test_push_pull_search_roundtrip(directory: AgentDirectory):
    """Full lifecycle: push → pull → search → extract card."""
    card = _unique_card(
        description="Full roundtrip E2E test agent",
    )

    # 1. Push
    cid = await directory.push_agent_record(card)
    assert cid
    print(f"\n  1. Pushed '{card.name}' → CID: {cid}")

    # 2. Pull as OASF
    oasf_dict = await directory.pull_agent_record(cid)
    assert oasf_dict["name"] == card.name
    print(f"  2. Pulled OASF — name: {oasf_dict['name']}")

    # 3. Search
    results = await directory.search_agent_records(card.name)
    assert any(r.get("name") == card.name for r in results)
    print(f"  3. Search found {len(results)} result(s)")

    # 4. Extract card from pulled OASF
    restored = oasf_to_agent_card(oasf_dict)
    assert restored is not None
    assert restored.name == card.name
    assert restored.version == card.version
    print(f"  4. Extracted AgentCard: {restored.name} v{restored.version}")

    print("  === Full roundtrip PASSED ===")
