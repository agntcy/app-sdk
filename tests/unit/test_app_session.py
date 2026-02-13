# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from tests.server.a2a_server import default_a2a_server
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_app_session():
    """
    Unit test for the AgntcyFactory and its components using the fluent API.
    """

    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=1)

    # Create an app container via the fluent builder
    app_session.add(default_a2a_server).with_topic("test/topic").with_session_id(
        "test_session"
    ).build()

    retrieved_container = app_session.get_app_container("test_session")

    assert retrieved_container is not None, "Failed to retrieve the app container."
    assert retrieved_container.topic == "test/topic", "Topic mismatch."

    # test adding > max_sessions
    try:
        app_session.add(default_a2a_server).with_topic("test/topic2").with_session_id(
            "invalid_test_session"
        ).build()
        assert False, "Max sessions should have been reached"
    except Exception:
        pass

    # test removing app container
    app_session.remove_app_container("test_session")
    assert (
        app_session.get_app_container("test_session") is None
    ), "App container was not removed properly."
