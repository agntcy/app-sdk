# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for :class:`NatsTransport` connection setup."""

import pytest
from nats.aio.client import Client

from agntcy_app_sdk.transport.nats.transport import NatsTransport

pytest_plugins = "pytest_asyncio"


@pytest.mark.asyncio
async def test_setup_without_endpoint_and_disconnected_client_raises() -> None:
    transport = NatsTransport(client=Client())

    with pytest.raises(RuntimeError, match="no endpoint was given"):
        await transport.setup()


@pytest.mark.asyncio
async def test_reconnected_cb_without_connected_url_does_not_raise() -> None:
    """nats-py runs this callback inside its reconnect task; it must not raise."""
    await NatsTransport(client=Client()).reconnected_cb()
