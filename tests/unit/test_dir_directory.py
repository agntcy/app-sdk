# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import pytest
from agntcy_app_sdk.directory.base import RecordVisibility
from agntcy_app_sdk.directory.dir.agent_directory import AgntcyAgentDirectory
from agntcy.dir_sdk.models import core_v1


def generate_record(name, skill_id=10201, domain_id=103):
    """Generate a sample agent record for testing."""
    return core_v1.Record(
        data={
            "name": name,
            "version": "v1.0.0",
            "schema_version": "0.7.0",
            "description": f"Test agent: {name}",
            "authors": ["AGNTCY"],
            "created_at": "2025-03-19T17:06:37Z",
            "skills": [
                {
                    "name": "natural_language_processing/natural_language_generation/text_completion",
                    "id": skill_id,
                },
                {
                    "name": "natural_language_processing/analytical_reasoning/problem_solving",
                    "id": 10702,
                },
            ],
            "locators": [
                {
                    "type": "docker_image",
                    "url": f"https://ghcr.io/agntcy/{name.lower()}",
                }
            ],
            "domains": [{"name": "technology/networking", "id": domain_id}],
        },
    )


@pytest.fixture
def directory():
    """Fixture to provide an AgntcyAgentDirectory instance with real client."""
    return AgntcyAgentDirectory(
        server_address="localhost:8888", dirctl_path="/usr/local/bin/dirctl"
    )


@pytest.fixture
def sample_record():
    """Fixture to provide a sample agent record."""
    return generate_record("test-agent")


@pytest.fixture
def cleanup_refs():
    """Fixture to track and cleanup created records."""
    refs_to_cleanup = []
    yield refs_to_cleanup
    # Cleanup will happen in individual tests if needed


class TestAgntcyAgentDirectoryInit:
    """Tests for initialization."""

    def test_init_with_defaults(self):
        """Test initialization with default parameters."""
        directory = AgntcyAgentDirectory()
        assert directory.client is not None

    def test_init_with_custom_params(self):
        """Test initialization with custom parameters."""
        directory = AgntcyAgentDirectory(
            server_address="localhost:8888", dirctl_path="/usr/local/bin/dirctl"
        )
        assert directory.client is not None


class TestPushAgentRecord:
    """Tests for push_agent_record method."""

    @pytest.mark.asyncio
    async def test_push_public_record(self, directory, cleanup_refs):
        """Test pushing a public agent record."""
        record = generate_record("public-test-agent")

        cid = await directory.push_agent_record(
            record, visibility=RecordVisibility.PUBLIC
        )
        print("Pushed public record CID:", cid, type(cid))
        cleanup_refs.append(cid)

        assert cid is not None

    @pytest.mark.asyncio
    async def test_push_private_record(self, directory, cleanup_refs):
        """Test pushing a private agent record."""
        record = generate_record("private-test-agent")

        cid = await directory.push_agent_record(
            record, visibility=RecordVisibility.PRIVATE
        )
        cleanup_refs.append(cid)

        assert cid is not None
        # assert isinstance(cid, str)

    @pytest.mark.asyncio
    async def test_push_multiple_records(self, directory, cleanup_refs):
        """Test pushing multiple records."""
        record1 = generate_record("agent-1")
        record2 = generate_record("agent-2")

        cid1 = await directory.push_agent_record(record1)
        cid2 = await directory.push_agent_record(record2)

        cleanup_refs.extend([cid1, cid2])

        assert cid1 != cid2
        assert cid1 is not None
        assert cid2 is not None


class TestPullAgentRecord:
    """Tests for pull_agent_record method."""

    @pytest.mark.asyncio
    async def test_pull_existing_record(self, directory, cleanup_refs):
        """Test pulling an existing agent record."""
        # First push a record
        record = generate_record("pull-test-agent")
        cid = await directory.push_agent_record(record)

        print("Pushed record CID for pull test:", cid, type(cid))
        cleanup_refs.append(cid)

        # Then pull it back
        pulled_record = await directory.pull_agent_record(cid)

        assert pulled_record is not None
        assert pulled_record.data["name"] == "pull-test-agent"

    @pytest.mark.asyncio
    async def test_pull_nonexistent_record(self, directory):
        """Test pulling a non-existent agent record."""
        fake_ref = "nonexistent-cid-12345"
        try:
            result = await directory.pull_agent_record(fake_ref)
        except Exception:
            result = None

        assert result is None


class TestDeleteAgentRecord:
    """Tests for delete_agent_record method."""

    @pytest.mark.asyncio
    async def test_delete_record(self, directory):
        """Test deleting an agent record."""
        # Push a record first
        record = generate_record("delete-test-agent")
        cid = await directory.push_agent_record(record)

        # Delete it
        await directory.delete_agent_record(cid)

        # Verify it's deleted
        # pulled = await directory.pull_agent_record(cid)
        # assert pulled is None


class TestPushPullDeleteCycle:
    """Integration tests for complete push-pull-delete cycle."""

    @pytest.mark.asyncio
    async def test_complete_lifecycle(self, directory):
        """Test complete lifecycle: push, pull, verify, delete."""
        # Push
        record = generate_record("lifecycle-agent")
        cid = await directory.push_agent_record(
            record, visibility=RecordVisibility.PUBLIC
        )
        assert cid is not None

        # Pull and verify
        pulled = await directory.pull_agent_record(cid)
        assert pulled is not None
        assert pulled.data["name"] == "lifecycle-agent"
        assert pulled.data["version"] == "v1.0.0"

        # Delete
        await directory.delete_agent_record(cid)

        # Verify deletion
        # deleted = await directory.pull_agent_record(cid)
        # assert deleted is None


class TestListAgentRecords:
    """Tests for list_agent_records method."""

    @pytest.mark.asyncio
    async def test_list_records(self, directory):
        """Test listing agent records."""
        result = await directory.list_agent_records()

        print("Listed records:", result)

        assert isinstance(result, list)
        # Note: The actual results depend on what's in the directory


class TestSearchAgentRecords:
    """Tests for search_agent_records method."""

    @pytest.mark.asyncio
    async def test_search_records(self, directory):
        """Test searching for agent records."""
        result = await directory.search_agent_records("test-query", limit=3)

        assert isinstance(result, list)
        # Note: The actual results depend on what's in the directory


class TestSetRecordVisibility:
    """Tests for set_record_visibility method."""

    @pytest.mark.asyncio
    async def test_set_visibility_to_public(self, directory, cleanup_refs):
        """Test setting record visibility to public."""
        # Push a private record
        record = generate_record("visibility-test-public")
        cid = await directory.push_agent_record(
            record, visibility=RecordVisibility.PRIVATE
        )
        cleanup_refs.append(cid)

        # Change to public
        result = await directory.set_record_visibility(cid, RecordVisibility.PUBLIC)

        assert result is None  # Success returns None

    @pytest.mark.asyncio
    async def test_set_visibility_to_private(self, directory, cleanup_refs):
        """Test setting record visibility to private."""
        # Push a public record
        record = generate_record("visibility-test-private")
        cid = await directory.push_agent_record(
            record, visibility=RecordVisibility.PUBLIC
        )
        cleanup_refs.append(cid)

        # Change to private
        result = await directory.set_record_visibility(cid, RecordVisibility.PRIVATE)

        assert result is None  # Success returns None

    @pytest.mark.asyncio
    async def test_set_visibility_record_not_found(self, directory):
        """Test setting visibility when record doesn't exist."""
        fake_ref = "nonexistent-ref-12345"

        result = await directory.set_record_visibility(
            fake_ref, RecordVisibility.PUBLIC
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_set_visibility_protected_not_supported(
        self, directory, cleanup_refs
    ):
        """Test that protected visibility raises NotImplementedError."""
        record = generate_record("protected-test")
        cid = await directory.push_agent_record(record)
        cleanup_refs.append(cid)

        with pytest.raises(NotImplementedError):
            await directory.set_record_visibility(cid, RecordVisibility.PROTECTED)


class TestNotImplementedMethods:
    """Tests for methods that are not yet implemented."""

    @pytest.mark.asyncio
    async def test_sign_agent_record_not_implemented(self, directory):
        """Test that sign_agent_record raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await directory.sign_agent_record("ref", "provider")

    @pytest.mark.asyncio
    async def test_verify_agent_record_not_implemented(self, directory):
        """Test that verify_agent_record raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await directory.verify_agent_record("ref")

    @pytest.mark.asyncio
    async def test_get_record_visibility_not_implemented(self, directory):
        """Test that get_record_visibility raises NotImplementedError."""
        with pytest.raises(NotImplementedError):
            await directory.get_record_visibility("ref")


class TestRecordGeneration:
    """Tests for the generate_record helper function."""

    def test_generate_basic_record(self):
        """Test generating a basic record."""
        record = generate_record("test-agent")

        assert record.data["name"] == "test-agent"
        assert record.data["version"] == "v1.0.0"
        assert record.data["schema_version"] == "0.7.0"
        assert "skills" in record.data
        assert len(record.data["skills"]) == 2

    def test_generate_record_with_custom_skill(self):
        """Test generating a record with custom skill ID."""
        record = generate_record("custom-agent", skill_id=99999)

        assert record.data["skills"][0]["id"] == 99999

    def test_generate_record_with_custom_domain(self):
        """Test generating a record with custom domain ID."""
        record = generate_record("custom-domain-agent", domain_id=88888)

        assert record.data["domains"][0]["id"] == 88888
