# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.transport.slim.transport import SLIMTransport

pytest_plugins = "pytest_asyncio"


def create_transport_with_mocks(
    message_timeout: datetime.timedelta,
) -> tuple[SLIMTransport, MagicMock, MagicMock]:
    transport = SLIMTransport(
        routable_name="test/client/requester",
        endpoint="http://localhost:46357",
        message_timeout=message_timeout,
    )
    transport._slim_app = AsyncMock()
    transport._slim_connection_id = 7

    response = Message(type="response", payload=b"ok")
    session = MagicMock()
    session.publish_async = AsyncMock()
    session.get_message_async = AsyncMock(
        return_value=SimpleNamespace(payload=response.serialize())
    )
    session.session_id.return_value = 123

    session_manager = MagicMock()
    session_manager.point_to_point_session = AsyncMock(return_value=session)
    session_manager.close_session = AsyncMock()
    transport._session_manager = session_manager

    return transport, session, session_manager


@pytest.mark.asyncio
async def test_request_uses_configured_message_timeout() -> None:
    configured_timeout = datetime.timedelta(seconds=45)
    transport, session, session_manager = create_transport_with_mocks(
        configured_timeout
    )

    response = await transport.request(
        "test/server/responder",
        Message(type="request", payload=b"hello"),
    )

    transport._slim_app.set_route_async.assert_awaited_once()
    route_call = transport._slim_app.set_route_async.await_args
    remote_name = route_call.args[0]

    assert route_call.args[1] == 7
    session_manager.point_to_point_session.assert_awaited_once_with(
        remote_name,
        timeout=configured_timeout,
    )
    session.get_message_async.assert_awaited_once_with(timeout=configured_timeout)
    session_manager.close_session.assert_awaited_once_with(session)
    assert response.payload == b"ok"


@pytest.mark.asyncio
async def test_request_timeout_overrides_configured_timeout() -> None:
    transport, session, session_manager = create_transport_with_mocks(
        datetime.timedelta(seconds=60)
    )

    response = await transport.request(
        "test/server/responder",
        Message(type="request", payload=b"hello"),
        timeout=12.5,
    )

    route_call = transport._slim_app.set_route_async.await_args
    remote_name = route_call.args[0]
    overridden_timeout = datetime.timedelta(seconds=12.5)

    session_manager.point_to_point_session.assert_awaited_once_with(
        remote_name,
        timeout=overridden_timeout,
    )
    session.get_message_async.assert_awaited_once_with(timeout=overridden_timeout)
    session_manager.close_session.assert_awaited_once_with(session)
    assert response.payload == b"ok"
