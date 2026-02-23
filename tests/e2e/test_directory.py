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
from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentCapabilities, AgentCard, AgentProvider, AgentSkill

from agntcy_app_sdk.app_sessions import AppContainer
from agntcy_app_sdk.directory import (
    AgentDirectory,
    agent_card_to_oasf,
    oasf_to_agent_card,
)
from agntcy_app_sdk.directory.oasf_converter import MODULE_NAME_A2A
from agntcy_app_sdk.factory import AgntcyFactory
from tests.server.agent_executor import HelloWorldAgentExecutor

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


# ---------------------------------------------------------------------------
# AppSession / AppContainer → Directory pipeline tests
# ---------------------------------------------------------------------------

DEFAULT_SKILL = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)


def _build_a2a_server(name: str = "dir-pipeline-agent") -> A2AStarletteApplication:
    """Build an A2A server whose AgentCard will be pushed to the directory."""
    card = _unique_card(
        name=name,
        description="E2E directory pipeline agent",
        capabilities=AgentCapabilities(streaming=True),
        skills=[DEFAULT_SKILL],
    )
    request_handler = DefaultRequestHandler(
        agent_executor=HelloWorldAgentExecutor(name),
        task_store=InMemoryTaskStore(),
    )
    return A2AStarletteApplication(agent_card=card, http_handler=request_handler)


@pytest.mark.asyncio
async def test_app_container_pushes_record_to_directory():
    """AppContainer.run() pushes the handler's AgentCard to the directory.

    Pipeline: build A2A server → create directory → wire via ContainerBuilder
    → run → verify record in directory → stop.
    """
    factory = AgntcyFactory()

    # 1. Build A2A server with a unique agent card
    server = _build_a2a_server()
    original_card = server.agent_card
    print(f"\n  1. Built A2A server — card.name: {original_card.name}")

    # 2. Create directory via factory
    directory = factory.create_directory("agntcy", endpoint=DIR_SERVER_ADDRESS)

    # 3. Wire through AppSession + ContainerBuilder (JSONRPC — no transport)
    session = factory.create_app_session()
    container = (
        session.add(server)
        .with_directory(directory)
        .with_port(9020)
        .with_session_id("dir-pipeline-test")
        .build()
    )

    assert isinstance(container, AppContainer)
    assert container.directory is directory
    print("  2. Built AppContainer with directory attached")

    # 4. Run — this triggers handler.setup() → directory.setup() → push_agent_record()
    await container.run(keep_alive=False)
    assert container.is_running
    assert (
        container.directory_cid is not None
    ), "directory_cid should be set after run()"
    print(f"  3. Container started — CID: {container.directory_cid}")

    # 5. Search the directory for the pushed card by name
    results = await directory.search_agent_records(original_card.name, limit=5)
    assert len(results) >= 1, (
        f"Expected at least 1 search result for '{original_card.name}', "
        f"got {len(results)}"
    )

    names = [r.get("name") for r in results]
    assert (
        original_card.name in names
    ), f"Expected '{original_card.name}' in search results, got: {names}"
    print(f"  4. Directory search confirmed — found '{original_card.name}'")

    # 6. Pull and verify the card fields
    pulled_oasf = results[0]
    restored_card = oasf_to_agent_card(pulled_oasf)
    assert restored_card is not None
    assert restored_card.name == original_card.name
    assert restored_card.version == original_card.version
    print(f"  5. Extracted card: {restored_card.name} v{restored_card.version}")

    # 7. Stop — triggers directory.teardown()
    await container.stop()
    assert not container.is_running
    print("  6. Container stopped — directory teardown complete")

    print("  === AppContainer pipeline PASSED ===")


@pytest.mark.asyncio
async def test_app_session_start_all_pushes_records():
    """AppSession.start_all_sessions() pushes records for all containers.

    Verifies that wiring multiple containers with directories through an
    AppSession pushes each container's AgentCard to the directory.
    """
    factory = AgntcyFactory()

    # Build two A2A servers with unique names
    server_a = _build_a2a_server()
    server_b = _build_a2a_server()
    card_a = server_a.agent_card
    card_b = server_b.agent_card
    print(f"\n  1. Built servers — {card_a.name}, {card_b.name}")

    # Create a shared directory
    directory = factory.create_directory("agntcy", endpoint=DIR_SERVER_ADDRESS)

    # Wire both through the same AppSession (different ports to avoid conflict)
    session = factory.create_app_session(max_sessions=5)
    session.add(server_a).with_directory(directory).with_port(9010).with_session_id(
        "dir-a"
    ).build()
    session.add(server_b).with_directory(directory).with_port(9011).with_session_id(
        "dir-b"
    ).build()
    print("  2. Built 2 AppContainers with shared directory")

    # Start all
    await session.start_all_sessions(keep_alive=False)
    print("  3. All sessions started")

    # Verify both containers got CIDs and pull each record back by CID
    container_a = session.get_app_container("dir-a")
    container_b = session.get_app_container("dir-b")
    for label, container, card in (
        ("A", container_a, card_a),
        ("B", container_b, card_b),
    ):
        assert (
            container.directory_cid is not None
        ), f"Container {label} should have a directory_cid after run()"
        cid = container.directory_cid
        print(f"  4{label}. Container {label} CID: {cid}")

        # Pull by CID and extract the AgentCard
        pulled = await directory.pull_agent_record(cid, extract_card=True)
        assert (
            pulled is not None
        ), f"pull_agent_record({cid}) returned None for container {label}"
        assert isinstance(
            pulled, AgentCard
        ), f"Expected AgentCard, got {type(pulled).__name__}"
        assert pulled.name == card.name
        assert pulled.version == card.version
        print(f"  5{label}. Pulled & verified '{pulled.name}' v{pulled.version}")

    # Stop all
    await session.stop_all_sessions()
    for cid, c in session.app_containers.items():
        assert not c.is_running, f"Container {cid} should be stopped"
    print("  5. All sessions stopped")

    print("  === AppSession multi-container pipeline PASSED ===")


@pytest.mark.asyncio
async def test_app_container_no_directory_skips_push():
    """When no directory is configured, run() succeeds without pushing.

    Ensures the pipeline doesn't break when the directory is omitted.
    """
    factory = AgntcyFactory()
    server = _build_a2a_server()

    session = factory.create_app_session()
    container = (
        session.add(server).with_port(9030).with_session_id("no-dir-test").build()
    )

    assert container.directory is None

    # run() should succeed without error
    await container.run(keep_alive=False)
    assert container.is_running

    await container.stop()
    assert not container.is_running
    print("\n  === No-directory pipeline PASSED (no errors) ===")


@pytest.mark.asyncio
async def test_factory_create_directory_in_pipeline():
    """Verify factory.create_directory() integrates with the full pipeline.

    Uses the registry-based create_directory("agntcy", endpoint=...) API
    and ensures the returned directory works end-to-end.
    """
    factory = AgntcyFactory()

    # Verify registry introspection
    assert "agntcy" in factory.registered_directories()

    # Create directory via factory
    directory = factory.create_directory("agntcy", endpoint=DIR_SERVER_ADDRESS)
    assert isinstance(directory, AgentDirectory)

    # Wire through the pipeline and push
    server = _build_a2a_server()
    card = server.agent_card
    print(f"\n  1. Server card: {card.name}")

    session = factory.create_app_session()
    container = (
        session.add(server)
        .with_directory(directory)
        .with_port(9040)
        .with_session_id("factory-dir-test")
        .build()
    )

    await container.run(keep_alive=False)
    print("  2. Container started")

    # Pull from directory to confirm the push
    results = await directory.search_agent_records(card.name, limit=5)
    assert any(
        r.get("name") == card.name for r in results
    ), f"Card '{card.name}' not found in directory after run()"
    print(f"  3. Confirmed '{card.name}' in directory")

    await container.stop()
    print("  4. Container stopped")

    print("  === Factory create_directory pipeline PASSED ===")
