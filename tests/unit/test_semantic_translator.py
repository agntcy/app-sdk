# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from agntcy.dir_sdk.models import core_v1

from agntcy_app_sdk.factory import TransportTypes
from a2a.types import AgentCapabilities, AgentCard, AgentSkill
from agntcy_app_sdk.semantic.translator import SemanticTranslator


@pytest.fixture
def valid_oasf_record():
    """Fixture for a valid OASF record."""
    return {
        "name": "example.org/my-agent",
        "schema_version": "0.7.0",
        "version": "v1.0.0",
        "description": "An example agent for demonstration",
        "authors": ["Your Name <your.email@example.com>"],
        "created_at": "2025-01-01T00:00:00Z",
        "domains": [{"id": 101, "name": "technology/internet_of_things"}],
        "locators": [
            {"type": "docker_image", "url": "ghcr.io/example/my-agent:latest"}
        ],
        "skills": [
            {
                "name": "natural_language_processing/natural_language_understanding",
                "id": 101,
            }
        ],
    }


@pytest.fixture
def valid_oasf_corev1_record():
    """Fixture for a valid OASF CoreV1 record."""
    return core_v1.Record(
        data={
            "name": "Example Agent",
            "version": "v1.0.0",
            "schema_version": "0.7.0",
            "description": "Test agent: Example Agent",
            "authors": ["AGNTCY"],
            "created_at": "2025-03-19T17:06:37Z",
            "skills": [
                {
                    "name": "natural_language_processing/natural_language_generation/text_completion",
                    "id": 10201,
                },
                {
                    "name": "natural_language_processing/analytical_reasoning/problem_solving",
                    "id": 10702,
                },
            ],
            "locators": [
                {
                    "type": "docker_image",
                    "url": "https://ghcr.io/agntcy/example-agent:latest",
                }
            ],
            "domains": [{"name": "technology/networking", "id": 103}],
        },
    )


@pytest.fixture
def agent_skill():
    """Fixture for an AgentSkill."""
    return AgentSkill(
        id="get_accounting_status",
        name="Get Accounting Status",
        description="Returns the accounting / payment status of coffee bean orders.",
        tags=["coffee", "accounting", "payments"],
        examples=[
            "Has the order moved from CUSTOMS_CLEARANCE to PAYMENT_COMPLETE yet?",
            "Confirm payment completion for the Colombia shipment.",
            "Did the Brazil order clear CUSTOMS_CLEARANCE and get marked PAYMENT_COMPLETE?",
            "Is any payment still pending after CUSTOMS_CLEARANCE?",
            "Mark the 50 lb Colombia order as PAYMENT_COMPLETE if customs is cleared.",
        ],
    )


@pytest.fixture
def agent_card(agent_skill):
    """Fixture for an AgentCard."""

    skill = AgentSkill(
        id="get_accounting_status",
        name="Get Accounting Status",
        description="Returns the accounting / payment status of coffee bean orders.",
        tags=["coffee", "accounting", "payments"],
        examples=[
            "Has the order moved from CUSTOMS_CLEARANCE to PAYMENT_COMPLETE yet?",
            "Confirm payment completion for the Colombia shipment.",
            "Did the Brazil order clear CUSTOMS_CLEARANCE and get marked PAYMENT_COMPLETE?",
            "Is any payment still pending after CUSTOMS_CLEARANCE?",
            "Mark the 50 lb Colombia order as PAYMENT_COMPLETE if customs is cleared.",
        ],
    )

    return AgentCard(  # type: ignore
        name="Accountant agent",
        description="An AI agent that confirms the payment.",
        url="",
        additional_interfaces=[
            {
                "transport": TransportTypes.NATS.value,
                "url": "agents.hello_world.nats",
            },
            {
                "transport": TransportTypes.SLIM.value,
                "url": "agents.hello_world.slim",
            },
        ],
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True),
        skills=[skill],
        supports_authenticated_extended_card=False,
    )


@pytest.fixture
def translator():
    """Fixture for SemanticTranslator with context manager."""
    with SemanticTranslator() as t:
        yield t


def test_validate_oasf_valid_record(translator, valid_oasf_record):
    """Test validation of a valid OASF record."""
    is_valid, errors = translator.validate_oasf(record_data=valid_oasf_record)

    assert is_valid is True
    assert len(errors) == 0


def test_dir_sdk_to_oasf_sdk_record_conversion(translator, valid_oasf_corev1_record):
    """Test conversion from dir_sdk core_v1.Record to OASF record data dictionary."""
    record_struct = translator._dir_sdk_record_to_oasf_sdk_record(
        valid_oasf_corev1_record
    )
    record_data = dict(record_struct)

    assert record_data is not None
    assert isinstance(record_data, dict)
    assert record_data["name"] == "Example Agent"

    # convert back to core_v1.Record to verify roundtrip
    record_back = translator._oasf_sdk_record_to_dir_sdk_record(record_struct)
    assert record_back is not None
    assert isinstance(record_back, core_v1.Record)
    assert record_back.data["name"] == "Example Agent"


def test_a2a_to_oasf_translation(translator, agent_card):
    """Test translation from A2A AgentCard to OASF record."""
    oasf_record = translator.a2a_to_oasf(agent_card)

    assert oasf_record is not None
    assert isinstance(oasf_record, core_v1.Record)


def test_roundtrip_translation(translator, agent_card):
    """Test roundtrip translation: A2A -> OASF -> A2A."""
    # A2A to OASF
    oasf_record = translator.a2a_to_oasf(agent_card)
    assert oasf_record is not None

    # OASF back to A2A
    a2a_card = translator.oasf_to_a2a(oasf_record)
    assert a2a_card is not None
    assert isinstance(a2a_card, AgentCard)

    print(json.dumps(a2a_card.model_dump(), indent=2))


def test_roundtrip_translation_short(translator, agent_card):
    """Test roundtrip translation: A2A -> OASF -> A2A (short version)."""
    oasf_record = translator.translate_from("A2A", agent_card)
    a2a_card = translator.translate_to("A2A", oasf_record)
    assert a2a_card is not None
    assert isinstance(a2a_card, AgentCard)


def test_connection_error_when_not_connected():
    """Test that methods raise error when not connected."""
    translator = SemanticTranslator(auto_connect=False)

    with pytest.raises(Exception) as exc_info:
        translator.validate_oasf({"test": "data"})

    assert "not connected" in str(exc_info.value).lower()


def test_context_manager_cleanup(valid_oasf_record):
    """Test that context manager properly cleans up resources."""
    with SemanticTranslator() as translator:
        # Should work inside context
        is_valid, _ = translator.validate_oasf(valid_oasf_record)
        assert translator._channel is not None

    # Channel should be closed after context exit
    assert translator._channel is None


def test_manual_connect_and_close(valid_oasf_record):
    """Test manual connection lifecycle management."""
    translator = SemanticTranslator(auto_connect=False)
    assert translator._channel is None

    translator.connect()
    assert translator._channel is not None

    is_valid, _ = translator.validate_oasf(valid_oasf_record)
    assert is_valid is not None

    translator.close()
    assert translator._channel is None
