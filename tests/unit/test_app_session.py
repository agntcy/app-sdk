# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_sessions import AppContainer
from tests.server.a2a_server import default_a2a_server
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_app_session():
    """
    Unit test for the AgntcyFactory and its components.
    """

    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=1)

    # Create an app container
    container = AppContainer(
        server=default_a2a_server,
        transport=None,
        directory=None,
        topic="test/topic",
    )
    app_session.add_app_container("test_session", container)
    retrieved_container = app_session.get_app_container("test_session")

    assert retrieved_container is not None, "Failed to retrieve the app container."
    assert retrieved_container.topic == "test/topic", "Topic mismatch."

    # test adding > max_sessions
    container = AppContainer(
        server=default_a2a_server,
        transport=None,
        directory=None,
        topic="test/topic",
    )
    try:
        app_session.add_app_container("invalid_test_session", container)
        assert False, "Max sessions should have been reached"
    except Exception as _:
        pass

    # test removing app container
    app_session.remove_app_container("test_session")
    assert (
        app_session.get_app_container("test_session") is None
    ), "App container was not removed properly."
