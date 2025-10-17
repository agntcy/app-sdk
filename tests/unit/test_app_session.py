# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.factory import AgntcyFactory
from agntcy_app_sdk.app_session import AppContainer
import pytest

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_app_session():
    """
    Unit test for the AgntcyFactory and its components.
    """

    factory = AgntcyFactory()
    app_session = factory.create_app_session(max_sessions=5)

    # Create an app container
    container = AppContainer(
        transport=None,
        protocol_handler=None,
        directory=None,
        topic="test/topic",
    )
    app_session.add_app_container("test_session", container)
    retrieved_container = app_session.get_app_container("test_session")

    assert retrieved_container is not None, "Failed to retrieve the app container."
    assert retrieved_container.topic == "test/topic", "Topic mismatch."

    # test removing app container
    app_session.remove_app_container("test_session")
    assert (
        app_session.get_app_container("test_session") is None
    ), "App container was not removed properly."
