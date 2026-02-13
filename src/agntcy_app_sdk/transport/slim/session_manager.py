# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from typing import Dict
import datetime
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
import slim_bindings
from slim_bindings import (
    Name,
    Session,
    SessionConfig,
    SessionType,
)

configure_logging()
logger = get_logger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}
        self._slim = None
        self._slim_connection_id = None
        self._lock = asyncio.Lock()

    def set_slim(self, slim: slim_bindings.App, slim_connection_id: int):
        """
        Set the SLIM client instance and SLIM connection id for the session manager.
        """
        self._slim = slim
        self._slim_connection_id = slim_connection_id

    async def point_to_point_session(
        self,
        remote_name: Name,
        max_retries: int = 5,
        timeout: datetime.timedelta = datetime.timedelta(seconds=5),
        mls_enabled: bool = True,
    ):
        """
        Create a new point-to-point session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        async with self._lock:
            point_to_point_session_ctx = await self._slim.create_session_async(
                SessionConfig(
                    session_type=SessionType.POINT_TO_POINT,
                    max_retries=max_retries,
                    interval=timeout,
                    enable_mls=mls_enabled,
                    metadata={},
                ),
                remote_name,
            )
            # Wait for session to be established
            await point_to_point_session_ctx.completion.wait_async()

            return point_to_point_session_ctx.session

    async def group_broadcast_session(
        self,
        channel: Name,
        invitees: list[Name],
        max_retries: int = 20,
        timeout: datetime.timedelta = datetime.timedelta(seconds=60),
        mls_enabled: bool = True,
    ):
        """
        Create a new group broadcast session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        # check if we already have a group broadcast session for this channel and invitees
        session_key = f"SessionType.Group:{channel}:" + ",".join(
            [str(invitee) for invitee in invitees]
        )

        # use the same lock for session creation and lookup
        async with self._lock:
            if session_key in self._sessions:
                logger.info(f"Reusing existing group broadcast session: {session_key}")
                return session_key, self._sessions[session_key]

            logger.debug(f"Creating new group broadcast session: {session_key}")
            group_session_ctx = await self._slim.create_session_async(
                SessionConfig(
                    session_type=SessionType.GROUP,
                    max_retries=max_retries,
                    interval=timeout,
                    enable_mls=mls_enabled,
                    metadata={},
                ),
                channel,
            )

            # Wait for session to be established
            await group_session_ctx.completion.wait_async()

            group_session = group_session_ctx.session
            for invitee in invitees:
                try:
                    logger.debug(
                        f"Inviting {invitee} to session {group_session.session_id()}"
                    )
                    await self._slim.set_route_async(invitee, self._slim_connection_id)
                    invite_handle = await group_session.invite_async(invitee)
                    await (
                        invite_handle.wait_async()
                    )  # guarantee that the invitee is invited to the group successfully
                    logger.debug(
                        f"Invited {invitee} to session {group_session.session_id()}"
                    )
                except Exception as e:
                    logger.error(f"Failed to invite {invitee}: {e}")

            # store the session info
            self._sessions[session_key] = group_session
            return session_key, group_session

    async def close_session(self, session: Session):
        """
        Close and remove a session.
        Args:
            session (PySession): The PySession object to close.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        session_id = session.session_id()

        try:
            # Removing session from local cache must be done before the actual session deletion from SLIM,
            # otherwise it would result in "session already closed" error since SLIM doesn't allow accessing
            # properties on a closed session.
            logger.debug(f"Attempting to remove session {session_id} from local cache.")
            await self._local_cache_cleanup(session_id)

            logger.debug(f"Attempting to delete session {session_id} from SLIM server.")
            delete_session_handle = await self._slim.delete_session_async(session)
            await delete_session_handle.wait_async()

            logger.info(f"Session {session_id} deleted successfully.")
        except asyncio.TimeoutError:
            logger.warning(
                f"Timed out while trying to delete session {session_id}. "
                f"It might still have been deleted on SLIM server, but no confirmation was received."
            )
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {e}")

    async def _local_cache_cleanup(self, session_id: int):
        """
        Perform local cleanup of a session without attempting to close it on the SLIM client.
        """
        async with self._lock:
            session_key = None
            for key, sess in self._sessions.items():
                if sess.session_id() == session_id:
                    session_key = key
                    break

            if session_key:
                del self._sessions[session_key]
                logger.info(f"Locally cleaned up session: {session_id}")
            else:
                logger.warning(
                    f"Session {session_id} cannot be removed from "
                    f"local cache since this session was not found."
                )

    def session_details(self, session_key: str):
        """
        Retrieve details of a session by its key.
        """
        session = self._sessions.get(session_key)
        if session:
            print(dir(session))
            return {
                "id": session.session_id(),
            }
        return None
