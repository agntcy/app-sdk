# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the AgentDirectory concrete class."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from a2a.types import AgentCapabilities, AgentCard
from agntcy.dir_sdk.models import core_v1, search_v1
from google.protobuf.json_format import ParseDict

from agntcy_app_sdk.directory.dir.agent_directory import AgentDirectory
from agntcy_app_sdk.directory.oasf_converter import MODULE_NAME_A2A

pytest_plugins = "pytest_asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_card(**overrides) -> AgentCard:
    defaults = {
        "name": "test-agent",
        "url": "http://localhost:9000",
        "version": "1.0.0",
        "description": "A test agent",
        "capabilities": AgentCapabilities(),
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [],
    }
    defaults.update(overrides)
    return AgentCard(**defaults)


def _make_record_from_dict(data: dict) -> core_v1.Record:
    """Build a protobuf Record whose ``data`` Struct holds *data*."""
    rec = core_v1.Record()
    ParseDict(data, rec.data)
    return rec


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_creates_client():
    directory = AgentDirectory()
    with patch("agntcy_app_sdk.directory.dir.agent_directory.Client") as MockClient:
        await directory.setup()
        MockClient.assert_called_once()
        assert directory._client is not None


@pytest.mark.asyncio
async def test_setup_idempotent():
    directory = AgentDirectory()
    with patch("agntcy_app_sdk.directory.dir.agent_directory.Client") as MockClient:
        await directory.setup()
        await directory.setup()
        MockClient.assert_called_once()


# ---------------------------------------------------------------------------
# push_agent_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_push_agent_card():
    directory = AgentDirectory()
    mock_client = MagicMock()
    ref = core_v1.RecordRef(cid="Qm123abc")
    mock_client.push.return_value = [ref]
    directory._client = mock_client

    card = _minimal_card()
    cid = await directory.push_agent_record(card)

    assert cid == "Qm123abc"
    mock_client.push.assert_called_once()
    pushed_records = mock_client.push.call_args[0][0]
    assert len(pushed_records) == 1
    assert isinstance(pushed_records[0], core_v1.Record)


@pytest.mark.asyncio
async def test_push_raw_dict():
    directory = AgentDirectory()
    mock_client = MagicMock()
    ref = core_v1.RecordRef(cid="QmDict456")
    mock_client.push.return_value = [ref]
    directory._client = mock_client

    oasf_dict = {"name": "raw-agent", "modules": []}
    cid = await directory.push_agent_record(oasf_dict)

    assert cid == "QmDict456"
    mock_client.push.assert_called_once()


@pytest.mark.asyncio
async def test_push_unsupported_type():
    directory = AgentDirectory()
    directory._client = MagicMock()

    with pytest.raises(TypeError, match="Unsupported record type"):
        await directory.push_agent_record(42)


@pytest.mark.asyncio
async def test_push_without_setup():
    directory = AgentDirectory()
    with pytest.raises(RuntimeError, match="not connected"):
        await directory.push_agent_record(_minimal_card())


# ---------------------------------------------------------------------------
# pull_agent_record
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pull_by_cid():
    directory = AgentDirectory()
    mock_client = MagicMock()

    oasf_dict = {
        "name": "pulled-agent",
        "modules": [
            {
                "name": MODULE_NAME_A2A,
                "data": {
                    "card_data": {"name": "pulled-agent", "url": "http://x"},
                },
            }
        ],
    }
    mock_client.pull.return_value = [_make_record_from_dict(oasf_dict)]
    directory._client = mock_client

    result = await directory.pull_agent_record("QmPull789")
    assert isinstance(result, dict)
    assert result["name"] == "pulled-agent"
    mock_client.pull.assert_called_once()


@pytest.mark.asyncio
async def test_pull_extract_card():
    directory = AgentDirectory()
    mock_client = MagicMock()

    oasf_dict = {
        "name": "card-agent",
        "modules": [
            {
                "name": MODULE_NAME_A2A,
                "data": {
                    "card_data": {
                        "name": "card-agent",
                        "url": "http://card-agent",
                        "version": "2.0.0",
                        "description": "A card agent",
                        "capabilities": {},
                        "defaultInputModes": ["text"],
                        "defaultOutputModes": ["text"],
                        "skills": [],
                    },
                },
            }
        ],
    }
    mock_client.pull.return_value = [_make_record_from_dict(oasf_dict)]
    directory._client = mock_client

    result = await directory.pull_agent_record("QmCard", extract_card=True)
    assert isinstance(result, AgentCard)
    assert result.name == "card-agent"
    assert result.version == "2.0.0"


@pytest.mark.asyncio
async def test_pull_no_results():
    directory = AgentDirectory()
    mock_client = MagicMock()
    mock_client.pull.return_value = []
    directory._client = mock_client

    result = await directory.pull_agent_record("QmEmpty")
    assert result is None


# ---------------------------------------------------------------------------
# search_agent_records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_by_string():
    directory = AgentDirectory()
    mock_client = MagicMock()

    resp = search_v1.SearchRecordsResponse()
    ParseDict({"name": "found-agent"}, resp.record.data)
    mock_client.search_records.return_value = [resp]
    directory._client = mock_client

    results = await directory.search_agent_records("found-agent")
    assert len(results) == 1
    assert results[0]["name"] == "found-agent"

    # Verify the request was built with a name query
    call_args = mock_client.search_records.call_args[0][0]
    assert isinstance(call_args, search_v1.SearchRecordsRequest)
    assert call_args.queries[0].type == search_v1.RecordQueryType.RECORD_QUERY_TYPE_NAME
    assert call_args.queries[0].value == "found-agent"
    assert call_args.limit == 10


# ---------------------------------------------------------------------------
# Not-implemented stubs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_not_implemented_methods():
    directory = AgentDirectory()
    directory._client = MagicMock()

    with pytest.raises(NotImplementedError):
        await directory.delete_agent_record("ref")

    with pytest.raises(NotImplementedError):
        await directory.list_agent_records()

    with pytest.raises(NotImplementedError):
        await directory.sign_agent_record("ref", "provider")

    with pytest.raises(NotImplementedError):
        await directory.verify_agent_record("ref")

    with pytest.raises(NotImplementedError):
        await directory.get_record_visibility("ref")

    with pytest.raises(NotImplementedError):
        await directory.set_record_visibility("ref", "public")


# ---------------------------------------------------------------------------
# teardown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_teardown():
    directory = AgentDirectory()
    directory._client = MagicMock()

    await directory.teardown()
    assert directory._client is None

    with pytest.raises(RuntimeError, match="not connected"):
        await directory.push_agent_record(_minimal_card())
