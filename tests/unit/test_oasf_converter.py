# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the OASF ↔ AgentCard converter."""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider

from agntcy_app_sdk.directory.oasf_converter import (
    CARD_SCHEMA_VERSION,
    DEFAULT_SKILL_ID,
    MODULE_ID_A2A,
    MODULE_NAME_A2A,
    OASF_SCHEMA_VERSION,
    agent_card_to_oasf,
    oasf_to_agent_card,
)

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_card(**overrides) -> AgentCard:
    defaults = {
        "name": "test-agent",
        "supported_interfaces": [
            AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9000")
        ],
        "version": "1.2.3",
        "description": "A test agent",
        "capabilities": AgentCapabilities(),
        "default_input_modes": ["text"],
        "default_output_modes": ["text"],
        "skills": [],
    }
    defaults.update(overrides)
    return AgentCard(**defaults)


# ---------------------------------------------------------------------------
# agent_card_to_oasf
# ---------------------------------------------------------------------------


def test_agent_card_to_oasf_structure():
    """Verify that the OASF envelope has the expected top-level keys and module."""
    card = _minimal_card()
    oasf = agent_card_to_oasf(card)

    assert oasf["name"] == "test-agent"
    assert oasf["schema_version"] == OASF_SCHEMA_VERSION
    assert oasf["version"] == "1.2.3"
    assert isinstance(oasf["modules"], list)
    assert len(oasf["modules"]) == 1

    module = oasf["modules"][0]
    assert module["name"] == MODULE_NAME_A2A
    assert module["id"] == MODULE_ID_A2A
    assert module["data"]["card_schema_version"] == CARD_SCHEMA_VERSION
    assert module["data"]["card_data"]["name"] == "test-agent"

    # Skills must be non-empty for OASF validation
    assert len(oasf["skills"]) >= 1
    assert oasf["skills"][0]["id"] == DEFAULT_SKILL_ID


def test_agent_card_to_oasf_with_provider():
    """Provider organization should populate the authors list."""
    card = _minimal_card(
        provider=AgentProvider(organization="Acme Corp", url="https://acme.example.com")
    )
    oasf = agent_card_to_oasf(card)
    assert oasf["authors"] == ["Acme Corp"]


def test_agent_card_to_oasf_without_provider():
    """No provider → authors should fall back to the card name."""
    card = _minimal_card()
    oasf = agent_card_to_oasf(card)
    assert oasf["authors"] == ["test-agent"]


# ---------------------------------------------------------------------------
# oasf_to_agent_card
# ---------------------------------------------------------------------------


def test_oasf_to_agent_card_roundtrip():
    """card → OASF → card should preserve name, interfaces, version."""
    original = _minimal_card()
    oasf = agent_card_to_oasf(original)
    restored = oasf_to_agent_card(oasf)

    assert restored is not None
    assert restored.name == original.name
    assert [(i.protocol_binding, i.url) for i in restored.supported_interfaces] == [
        (i.protocol_binding, i.url) for i in original.supported_interfaces
    ]
    assert restored.version == original.version


def test_oasf_to_agent_card_preserves_interface_order():
    """supported_interfaces order encodes preference and must survive the roundtrip."""
    original = _minimal_card(
        supported_interfaces=[
            AgentInterface(protocol_binding="slimpatterns", url="slim://topic"),
            AgentInterface(protocol_binding="JSONRPC", url="http://localhost:9000"),
        ]
    )
    restored = oasf_to_agent_card(agent_card_to_oasf(original))

    assert restored is not None
    assert [i.protocol_binding for i in restored.supported_interfaces] == [
        "slimpatterns",
        "JSONRPC",
    ]


def test_oasf_to_agent_card_legacy_v03_card():
    """Records stored by SDK versions on a2a-sdk 0.3 (url / preferredTransport /
    additionalInterfaces) must still load: the url becomes the first interface."""
    oasf = {
        "modules": [
            {
                "name": MODULE_NAME_A2A,
                "data": {
                    "card_data": {
                        "name": "legacy-agent",
                        "description": "stored by an older SDK",
                        "version": "0.9.0",
                        "url": "http://legacy:9000/",
                        "preferredTransport": "JSONRPC",
                        "additionalInterfaces": [
                            {"transport": "slimpatterns", "url": "slim://legacy"}
                        ],
                        "capabilities": {},
                        "defaultInputModes": ["text"],
                        "defaultOutputModes": ["text"],
                        "skills": [],
                    }
                },
            }
        ]
    }

    restored = oasf_to_agent_card(oasf)

    assert restored is not None
    assert restored.name == "legacy-agent"
    assert [(i.protocol_binding, i.url) for i in restored.supported_interfaces] == [
        ("JSONRPC", "http://legacy:9000/"),
        ("slimpatterns", "slim://legacy"),
    ]


def test_oasf_to_agent_card_no_matching_module():
    """Wrong module name → should return None."""
    oasf = {
        "modules": [
            {
                "name": "some/other-module",
                "data": {"card_data": {"name": "x"}},
            }
        ]
    }
    assert oasf_to_agent_card(oasf) is None


def test_oasf_to_agent_card_empty_modules():
    """Empty modules list → should return None."""
    assert oasf_to_agent_card({"modules": []}) is None
    assert oasf_to_agent_card({}) is None
