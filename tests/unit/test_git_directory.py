# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import pytest
import json
import os
import tempfile
import shutil
from pathlib import Path
from git import Repo
from agntcy_app_sdk.directory.base import RecordVisibility
from agntcy_app_sdk.directory.git.agent_directory import GitAgentDirectory

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_dir():
    """Create a temporary directory for testing."""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path)


@pytest.fixture
def sample_record():
    """Sample agent record for testing."""
    return {
        "id": "agent-001",
        "name": "TestAgent",
        "description": "A test agent",
        "capabilities": ["chat", "search"],
    }


@pytest.fixture
def sample_record_no_id():
    """Sample record without explicit ID."""
    return {"name": "AnotherAgent", "type": "assistant"}


@pytest.fixture
def local_single_user(temp_dir):
    """Local repository with single user."""
    repo_path = temp_dir / "local_single"
    directory = GitAgentDirectory(
        repo_path=str(repo_path), holder_id="alice", auto_push=False
    )
    yield directory


@pytest.fixture
def local_multi_user(temp_dir):
    """Local repository with multiple users."""
    repo_path = temp_dir / "local_multi"
    alice = GitAgentDirectory(
        repo_path=str(repo_path), holder_id="alice", auto_push=False
    )
    bob = GitAgentDirectory(repo_path=str(repo_path), holder_id="bob", auto_push=False)
    yield alice, bob


@pytest.fixture
def remote_repo(temp_dir):
    """Create a bare git repository to act as remote."""
    remote_path = temp_dir / "remote.git"
    remote_path.mkdir()
    Repo.init(remote_path, bare=True)
    return remote_path


@pytest.fixture
def remote_single_user(temp_dir, remote_repo):
    """Remote repository with single user."""
    repo_path = temp_dir / "remote_single"
    directory = GitAgentDirectory(
        repo_path=str(repo_path),
        holder_id="alice",
        remote_url=str(remote_repo),
        auto_push=True,
    )
    yield directory


@pytest.fixture
def remote_multi_user(temp_dir, remote_repo):
    """Remote repository with multiple users."""
    # First user clones and sets up
    repo_path_alice = temp_dir / "remote_multi_alice"
    alice = GitAgentDirectory(
        repo_path=str(repo_path_alice),
        holder_id="alice",
        remote_url=str(remote_repo),
        auto_push=True,
    )

    # Alice makes an initial commit to establish the branch
    initial_file = repo_path_alice / "README.md"
    initial_file.write_text("# Agent Directory")
    alice.repo.index.add(["README.md"])
    alice.repo.index.commit("Initial commit")
    alice.repo.remote("origin").push(
        refspec=f"{alice.repo.active_branch.name}:{alice.repo.active_branch.name}"
    )

    # Second user clones the same remote
    repo_path_bob = temp_dir / "remote_multi_bob"
    bob = GitAgentDirectory(
        repo_path=str(repo_path_bob),
        holder_id="bob",
        remote_url=str(remote_repo),
        auto_push=True,
    )

    yield alice, bob


# ============================================================================
# Store API Tests - Local Single User
# ============================================================================


@pytest.mark.asyncio
async def test_push_record_local_single(local_single_user, sample_record):
    """Test pushing a record in local single user setup."""
    await local_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PUBLIC
    )

    # Verify file exists
    record_path = local_single_user._get_record_path(
        "agent-001", RecordVisibility.PUBLIC
    )
    assert record_path.exists()

    # Verify content
    content = json.loads(record_path.read_text())
    assert content["name"] == "TestAgent"


@pytest.mark.asyncio
async def test_pull_record_local_single(local_single_user, sample_record):
    """Test pulling a record in local single user setup."""
    await local_single_user.push_agent_record(sample_record)

    pulled_record = await local_single_user.pull_agent_record("agent-001")
    assert pulled_record["name"] == "TestAgent"
    assert pulled_record["id"] == "agent-001"


@pytest.mark.asyncio
async def test_delete_record_local_single(local_single_user, sample_record):
    """Test deleting a record in local single user setup."""
    await local_single_user.push_agent_record(sample_record)
    await local_single_user.delete_agent_record("agent-001")

    with pytest.raises(FileNotFoundError):
        await local_single_user.pull_agent_record("agent-001")


@pytest.mark.asyncio
async def test_push_record_without_id_local_single(
    local_single_user, sample_record_no_id
):
    """Test pushing a record without explicit ID generates hash ID."""
    await local_single_user.push_agent_record(sample_record_no_id)

    records = await local_single_user.list_agent_records()
    assert len(records) == 1
    assert records[0]["name"] == "AnotherAgent"


# ============================================================================
# Store API Tests - Local Multi User
# ============================================================================


@pytest.mark.asyncio
async def test_push_record_local_multi(local_multi_user, sample_record):
    """Test pushing records from multiple users in local setup."""
    alice, bob = local_multi_user

    # Alice pushes a record
    await alice.push_agent_record(sample_record)

    # Bob pushes a different record
    bob_record = {"id": "agent-002", "name": "BobAgent"}
    await bob.push_agent_record(bob_record)

    # Verify Alice's record
    alice_record = await alice.pull_agent_record("agent-001")
    assert alice_record["name"] == "TestAgent"

    # Verify Bob's record
    bob_record_pulled = await bob.pull_agent_record("agent-002")
    assert bob_record_pulled["name"] == "BobAgent"


@pytest.mark.asyncio
async def test_isolation_local_multi(local_multi_user, sample_record):
    """Test visibility rules: public/protected are visible, private is isolated."""
    alice, bob = local_multi_user

    # Alice creates records with different visibility levels
    await alice.push_agent_record(
        {"id": "alice-public", "name": "AlicePublic"},
        visibility=RecordVisibility.PUBLIC,
    )
    await alice.push_agent_record(
        {"id": "alice-protected", "name": "AliceProtected"},
        visibility=RecordVisibility.PROTECTED,
    )
    await alice.push_agent_record(
        {"id": "alice-private", "name": "AlicePrivate"},
        visibility=RecordVisibility.PRIVATE,
    )

    # Bob should see Alice's public and protected records, but not private
    bob_records = await bob.list_agent_records()
    bob_record_ids = [r["_id"] for r in bob_records]

    assert "alice-public" in bob_record_ids
    assert "alice-protected" in bob_record_ids
    assert "alice-private" not in bob_record_ids
    assert len(bob_records) == 2

    # Alice should see all her own records
    alice_records = await alice.list_agent_records()
    assert len(alice_records) == 3


# ============================================================================
# Store API Tests - Remote Single User
# ============================================================================


@pytest.mark.asyncio
async def test_push_record_remote_single(remote_single_user, sample_record):
    """Test pushing a public record syncs to remote."""
    await remote_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PUBLIC
    )

    # Verify local file exists
    record_path = remote_single_user._get_record_path(
        "agent-001", RecordVisibility.PUBLIC
    )
    assert record_path.exists()

    # Verify commit was made
    assert len(list(remote_single_user.repo.iter_commits())) > 0


@pytest.mark.asyncio
async def test_push_private_no_remote_sync(remote_single_user, sample_record):
    """Test that private records are not pushed to remote."""
    await remote_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PRIVATE
    )

    # Record should exist locally
    record_path = remote_single_user._get_record_path(
        "agent-001", RecordVisibility.PRIVATE
    )
    assert record_path.exists()

    # Note: In a real test, you'd verify the remote doesn't have this file
    # by cloning the remote and checking


# ============================================================================
# Store API Tests - Remote Multi User
# ============================================================================


@pytest.mark.asyncio
async def test_push_pull_remote_multi(remote_multi_user, sample_record):
    """Test multiple users pushing and pulling via remote."""
    alice, bob = remote_multi_user

    # Get the branch name
    branch_name = alice.repo.active_branch.name

    # Alice pushes a record (auto_push=True should push to remote)
    await alice.push_agent_record(sample_record)

    # Bob pulls from remote to get Alice's record
    bob.repo.remote("origin").pull(branch_name)

    # Bob should now see Alice's record
    bob_records = await bob.list_agent_records()
    assert len(bob_records) == 1
    assert bob_records[0]["_holder"] == "alice"

    # Bob pushes his own record
    bob_record = {"id": "agent-002", "name": "BobAgent"}
    await bob.push_agent_record(bob_record)

    # Alice pulls Bob's changes
    alice.repo.remote("origin").pull(branch_name)

    # Both should see both public records now
    alice_records = await alice.list_agent_records()
    bob_records = await bob.list_agent_records()

    assert len(alice_records) == 2
    assert len(bob_records) == 2

    # Verify cross-visibility
    alice_ids = [r["_id"] for r in alice_records]
    bob_ids = [r["_id"] for r in bob_records]

    assert "agent-001" in alice_ids
    assert "agent-002" in alice_ids
    assert "agent-001" in bob_ids
    assert "agent-002" in bob_ids


# ============================================================================
# Search API Tests
# ============================================================================


@pytest.mark.asyncio
async def test_list_cross_user_visibility(local_multi_user):
    """Test that list_agent_records respects visibility across users."""
    alice, bob = local_multi_user

    # Setup records from both users
    await alice.push_agent_record(
        {"id": "alice-1", "name": "AlicePublic"}, RecordVisibility.PUBLIC
    )
    await bob.push_agent_record(
        {"id": "bob-1", "name": "BobPublic"}, RecordVisibility.PUBLIC
    )
    await bob.push_agent_record(
        {"id": "bob-2", "name": "BobPrivate"}, RecordVisibility.PRIVATE
    )

    # Alice should see: her public + bob's public (not bob's private)
    alice_view = await alice.list_agent_records()
    alice_ids = [r["_id"] for r in alice_view]
    assert "alice-1" in alice_ids
    assert "bob-1" in alice_ids
    assert "bob-2" not in alice_ids

    # Bob should see: his public + his private + alice's public
    bob_view = await bob.list_agent_records()
    bob_ids = [r["_id"] for r in bob_view]
    assert "bob-1" in bob_ids
    assert "bob-2" in bob_ids
    assert "alice-1" in bob_ids


@pytest.mark.asyncio
async def test_list_agent_records(local_single_user):
    """Test listing all agent records with visibility metadata."""
    record1 = {"id": "agent-001", "name": "Agent1"}
    record2 = {"id": "agent-002", "name": "Agent2"}

    await local_single_user.push_agent_record(record1, RecordVisibility.PUBLIC)
    await local_single_user.push_agent_record(record2, RecordVisibility.PRIVATE)

    records = await local_single_user.list_agent_records()
    assert len(records) == 2

    # Check visibility and holder metadata is included
    public_records = [r for r in records if r["_visibility"] == "public"]
    private_records = [r for r in records if r["_visibility"] == "private"]

    assert len(public_records) == 1
    assert len(private_records) == 1

    # All should have holder metadata
    assert all(r["_holder"] == "alice" for r in records)


@pytest.mark.asyncio
async def test_search_by_dict(local_single_user):
    """Test searching records by dictionary query."""
    record1 = {"id": "agent-001", "name": "Agent1", "type": "assistant"}
    record2 = {"id": "agent-002", "name": "Agent2", "type": "tool"}

    await local_single_user.push_agent_record(record1)
    await local_single_user.push_agent_record(record2)

    results = await local_single_user.search_agent_records({"type": "assistant"})
    assert len(results) == 1
    assert results[0]["name"] == "Agent1"


@pytest.mark.asyncio
async def test_search_by_text(local_single_user):
    """Test searching records by text query."""
    record1 = {"id": "agent-001", "name": "SearchableAgent"}
    record2 = {"id": "agent-002", "name": "OtherAgent"}

    await local_single_user.push_agent_record(record1)
    await local_single_user.push_agent_record(record2)

    results = await local_single_user.search_agent_records("Searchable")
    assert len(results) == 1
    assert results[0]["name"] == "SearchableAgent"


# ============================================================================
# Signing and Verification API Tests
# ============================================================================


@pytest.mark.asyncio
async def test_sign_agent_record(local_single_user, sample_record):
    """Test signing an agent record."""
    await local_single_user.push_agent_record(sample_record)
    await local_single_user.sign_agent_record("agent-001", "test-provider")

    # Check signature file exists
    sig_path = (
        local_single_user.repo_path
        / "agents"
        / "alice"
        / ".signatures"
        / "agent-001.sig.json"
    )
    assert sig_path.exists()

    sig_data = json.loads(sig_path.read_text())
    assert sig_data["record_id"] == "agent-001"
    assert sig_data["provider"] == "test-provider"


@pytest.mark.asyncio
async def test_verify_agent_record_signed(local_single_user, sample_record):
    """Test verifying a signed record."""
    await local_single_user.push_agent_record(sample_record)
    await local_single_user.sign_agent_record("agent-001", "test-provider")

    result = await local_single_user.verify_agent_record("agent-001")
    assert result["verified"] is True
    assert result["record_exists"] is True


@pytest.mark.asyncio
async def test_verify_agent_record_unsigned(local_single_user, sample_record):
    """Test verifying an unsigned record."""
    await local_single_user.push_agent_record(sample_record)

    result = await local_single_user.verify_agent_record("agent-001")
    assert result["verified"] is False
    assert result["reason"] == "No signature found"


@pytest.mark.asyncio
async def test_verify_nonexistent_record(local_single_user):
    """Test verifying a record that doesn't exist."""
    result = await local_single_user.verify_agent_record("nonexistent")
    assert result["verified"] is False


# ============================================================================
# Publishing API Tests
# ============================================================================


@pytest.mark.asyncio
async def test_get_record_visibility(local_single_user, sample_record):
    """Test getting record visibility."""
    await local_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PROTECTED
    )

    visibility = await local_single_user.get_record_visibility("agent-001")
    assert visibility == RecordVisibility.PROTECTED


@pytest.mark.asyncio
async def test_set_record_visibility(local_single_user, sample_record):
    """Test changing record visibility."""
    # Start with public
    await local_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PUBLIC
    )

    # Change to private
    result = await local_single_user.set_record_visibility(
        "agent-001", RecordVisibility.PRIVATE
    )
    assert result is True

    # Verify new visibility
    visibility = await local_single_user.get_record_visibility("agent-001")
    assert visibility == RecordVisibility.PRIVATE

    # Verify old location is empty
    old_path = local_single_user._get_record_path("agent-001", RecordVisibility.PUBLIC)
    assert not old_path.exists()

    # Verify new location has the record
    new_path = local_single_user._get_record_path("agent-001", RecordVisibility.PRIVATE)
    assert new_path.exists()


@pytest.mark.asyncio
async def test_set_visibility_same_level(local_single_user, sample_record):
    """Test setting visibility to the same level (no-op)."""
    await local_single_user.push_agent_record(
        sample_record, visibility=RecordVisibility.PUBLIC
    )

    result = await local_single_user.set_record_visibility(
        "agent-001", RecordVisibility.PUBLIC
    )
    assert result is True


# ============================================================================
# Edge Cases and Error Handling
# ============================================================================


@pytest.mark.asyncio
async def test_pull_nonexistent_record(local_single_user):
    """Test pulling a record that doesn't exist."""
    with pytest.raises(FileNotFoundError):
        await local_single_user.pull_agent_record("nonexistent")


@pytest.mark.asyncio
async def test_delete_nonexistent_record(local_single_user):
    """Test deleting a record that doesn't exist."""
    with pytest.raises(FileNotFoundError):
        await local_single_user.delete_agent_record("nonexistent")


@pytest.mark.asyncio
async def test_get_visibility_nonexistent(local_single_user):
    """Test getting visibility of nonexistent record."""
    with pytest.raises(FileNotFoundError):
        await local_single_user.get_record_visibility("nonexistent")


@pytest.mark.asyncio
async def test_multiple_visibility_levels(local_single_user):
    """Test records in different visibility levels."""
    record1 = {"id": "agent-001", "name": "PublicAgent"}
    record2 = {"id": "agent-002", "name": "PrivateAgent"}
    record3 = {"id": "agent-003", "name": "ProtectedAgent"}

    await local_single_user.push_agent_record(record1, RecordVisibility.PUBLIC)
    await local_single_user.push_agent_record(record2, RecordVisibility.PRIVATE)
    await local_single_user.push_agent_record(record3, RecordVisibility.PROTECTED)

    records = await local_single_user.list_agent_records()
    assert len(records) == 3

    visibilities = {r["_visibility"] for r in records}
    print("vis--", visibilities)
    assert visibilities == {"public", "private", "protected"}


# ============================================================================
# Integration Tests
# ============================================================================


@pytest.mark.asyncio
async def test_full_workflow_local(local_single_user):
    """Test complete workflow: create, update visibility, sign, verify, delete."""
    record = {"id": "workflow-test", "name": "WorkflowAgent"}

    # Create as private
    await local_single_user.push_agent_record(record, RecordVisibility.PRIVATE)

    # Change to public
    await local_single_user.set_record_visibility(
        "workflow-test", RecordVisibility.PUBLIC
    )

    # Sign the record
    await local_single_user.sign_agent_record("workflow-test", "test-provider")

    # Verify
    verification = await local_single_user.verify_agent_record("workflow-test")
    assert verification["verified"] is True

    # List and find it
    records = await local_single_user.list_agent_records()
    workflow_records = [r for r in records if r["id"] == "workflow-test"]
    assert len(workflow_records) == 1

    # Delete
    await local_single_user.delete_agent_record("workflow-test")

    # Verify deleted
    with pytest.raises(FileNotFoundError):
        await local_single_user.pull_agent_record("workflow-test")


# ============================================================================
# Real GitHub Repository Test
# ============================================================================


@pytest.mark.skipif(
    not os.environ.get("TEST_GIT_REPO_URL"),
    reason="TEST_GIT_REPO_URL environment variable not set",
)
@pytest.mark.asyncio
async def test_real_github_repo(temp_dir):
    """Test multi-user push, pull, and list with a real GitHub repository.

    Set TEST_GIT_REPO_URL to your GitHub repo URL with token:
    export TEST_GIT_REPO_URL="https://<token>@github.com/username/repo.git"
    """

    repo_url = os.environ.get("TEST_GIT_REPO_URL")
    alice_path = temp_dir / "github_alice"
    bob_path = temp_dir / "github_bob"

    print("\n🔧 Testing with GitHub repository")

    alice = GitAgentDirectory(
        repo_path=str(alice_path), holder_id="foo", remote_url=repo_url, auto_push=True
    )

    # Alice pushes a record (auto_push handles it)
    await alice.push_agent_record(
        {"id": "alice-agent", "name": "AliceAgent", "type": "assistant"},
        visibility=RecordVisibility.PUBLIC,
    )
    print("✅ Alice created and pushed record")

    bob = GitAgentDirectory(
        repo_path=str(bob_path), holder_id="bob", remote_url=repo_url, auto_push=True
    )

    # Bob lists records to get Alice's changes
    bob_records = await bob.list_agent_records()
    print(f"📋 Bob sees {len(bob_records)} record(s)")
    assert len(bob_records) > 0, f"Expected 1 record, got {len(bob_records)}"
    assert (
        bob_records[0]["name"] == "AliceAgent" or bob_records[0]["name"] == "BobAgent"
    )
    print("✅ Bob can see Alice's record")

    # Bob pushes his record (auto_push handles it)
    await bob.push_agent_record(
        {"id": "bob-agent", "name": "BobAgent", "type": "tool"},
        visibility=RecordVisibility.PUBLIC,
    )
    print("✅ Bob created and pushed record")

    # Alice lists with sync to get Bob's changes
    alice_records = await alice.list_agent_records()
    print(f"📋 Alice sees {len(alice_records)} record(s)")

    assert len(alice_records) > 1, f"Expected 2 records, got {len(alice_records)}"

    names = {r["name"] for r in alice_records}
    assert "AliceAgent" in names
    assert "BobAgent" in names

    # test delete
    await alice.delete_agent_record("alice-agent")

    alice_records_after_delete = await alice.list_agent_records()
    names_after_delete = {r["name"] for r in alice_records_after_delete}
    assert "AliceAgent" not in names_after_delete
    assert "BobAgent" in names_after_delete

    print("✅ GitHub test passed! Both users can collaborate")
    print(f"   Records: {names}")

    await bob.delete_agent_record("bob-agent")


@pytest.mark.skipif(
    not os.environ.get("TEST_GIT_REPO_URL"),
    reason="TEST_GIT_REPO_URL environment variable not set",
)
@pytest.mark.asyncio
async def test_real_github_repo_visibility(temp_dir):
    """Test multi-user push, pull, and list with a real GitHub repository.

    Set TEST_GIT_REPO_URL to your GitHub repo URL with token:
    export TEST_GIT_REPO_URL="https://<token>@github.com/username/repo.git"
    """

    repo_url = os.environ.get("TEST_GIT_REPO_URL")
    alice_path = temp_dir / "github_alice"
    bob_path = temp_dir / "github_bob"

    print("\n🔧 Testing with GitHub repository")

    alice = GitAgentDirectory(
        repo_path=str(alice_path),
        holder_id="alice",
        remote_url=repo_url,
        auto_push=True,
    )

    # Alice pushes a record (auto_push handles it)
    await alice.push_agent_record(
        {"id": "alice-agent", "name": "AliceAgent", "type": "assistant"},
        visibility=RecordVisibility.PRIVATE,
    )
    print("✅ Alice created and pushed record")

    bob = GitAgentDirectory(
        repo_path=str(bob_path), holder_id="bob", remote_url=repo_url, auto_push=True
    )

    # Bob lists records to get Alice's changes
    bob_records = await bob.list_agent_records()
    print(f"📋 Bob sees {len(bob_records)} record(s)")
    assert len(bob_records) == 0, f"Expected 0 record, got {len(bob_records)}"
