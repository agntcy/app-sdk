# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
from typing import Dict
import datetime
import random
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
import slim_bindings
from slim_bindings import (
    PyName,
    PySession,
    PySessionConfiguration,
)
from agntcy_app_sdk.transports.transport import Message
from threading import Lock

configure_logging()
logger = get_logger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, PySession] = {}
        self._slim = None
        self._lock = Lock()

    def set_slim(self, slim: slim_bindings.Slim):
        """
        Set the SLIM client instance for the session manager.
        """
        self._slim = slim

    async def point_to_point_session(
        self,
        remote_name: PyName,
        max_retries: int = 5,
        timeout: datetime.timedelta = datetime.timedelta(seconds=5),
        mls_enabled: bool = True,
    ):
        """
        Create a new point-to-point session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        # check if we already have a PointToPoint session
        for session_id, (session, q) in self._sessions.items():
            try:
                conf = await session.session_config()
                # compare the type of conf to PySessionConfiguration.PointToPoint
                if isinstance(conf, PySessionConfiguration.PointToPoint):
                    return session_id, session
            except Exception as e:
                # TODO: Revisit with SLIM team if this still exists in 0.5.0
                logger.debug(
                    f"could not retrieve SLIM session config for {session_id}: {e}"
                )
                continue

        with self._lock:
            session = await self._slim.create_session(
                PySessionConfiguration.PointToPoint(
                    peer_name=remote_name,
                    max_retries=max_retries,
                    timeout=timeout,
                    mls_enabled=mls_enabled,
                )
            )
            return session.id, session

    async def group_broadcast_session(
        self,
        channel: PyName,
        invitees: list[PyName],
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
        session_key = f"PySessionConfiguration.Group:{channel}:" + ",".join(
            [str(invitee) for invitee in invitees]
        )
        # use the same lock for session creation and lookup
        with self._lock:
            if session_key in self._sessions:
                logger.info(f"Reusing existing group broadcast session: {session_key}")
                return session_key, self._sessions[session_key]

            logger.debug(f"Creating new group broadcast session: {session_key}")
            created_session = await self._slim.create_session(
                PySessionConfiguration.Group(
                    channel_name=channel,
                    max_retries=max_retries,
                    timeout=timeout,
                    mls_enabled=mls_enabled,
                )
            )

            for invitee in invitees:
                try:
                    logger.debug(f"Inviting {invitee} to session {created_session.id}")
                    await self._slim.set_route(invitee)
                    await created_session.invite(invitee)
                except Exception as e:
                    logger.error(f"Failed to invite {invitee}: {e}")

            # store the created session
            self._sessions[session_key] = created_session
            return session_key, created_session

    async def close_session(self, session: PySession, end_signal: str = None):
        """
        Close and remove a session.

        Args:
            session (PySession): The PySession object to close.
            end_signal (str, optional): An optional signal message to send before closing.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set. Cannot close session.")

        session_deleted_server_side = False
        session_id = session.id
        try:
            # send the end signal to the remote if provided
            if end_signal is not None:
                logger.info(f"Sending end signal '{end_signal}' to remote {session.dst}")

                end_msg = Message(
                    type="text/plain",
                    headers={"x-session-end-message": end_signal},
                    payload=end_signal,
                )
                await session.publish(end_msg.serialize())

            logger.info(f"Waiting before attempting to delete session: {session.id}")
            # todo: proper way to wait for all messages to be processed
            await asyncio.sleep(
                random.uniform(5, 10)
            )  # add sleep before closing to allow for any in-flight messages to be processed
            logger.info(f"Attempting to delete session: {session.id}")

            # Sometimes SLIM delete_session can hang indefinitely but still deletes the session, so we add a timeout
            try:
                await asyncio.wait_for(self._slim.delete_session(session), timeout=5.0)
                logger.info(f"Session {session.id} deleted successfully within timeout.")
                session_deleted_server_side = True
            except asyncio.TimeoutError:
                logger.warning(f"Timed out while trying to delete session {session.id}. "
                               f"It might still have been deleted on SLIM server, but no confirmation was received.")
                session_deleted_server_side = True # Assume deletion might have happened, so clean up locally
            except Exception as e:
                logger.warning(f"Error deleting session {session.id} on SLIM server: {e}")
                session_deleted_server_side = False # Explicit error, assume not deleted on server

            # Clean up local cache only if SLIM server-side deletion was attempted and potentially successful/timed out
            if session_deleted_server_side:
                logger.debug(f"Removing session {session_id} from local cache.")
                self._local_cache_cleanup(session_id)
            else:
                logger.debug(f"Session {session_id} not removed from local cache due to confirmed server-side deletion failure.")

        except Exception as e:
            logger.warning(f"An error occurred during session closure or cleanup: {e}")
            return

    def _local_cache_cleanup(self, session_id: int):
        """
        Perform local cleanup of a session without attempting to close it on the SLIM client.
        """
        with self._lock:
            session_key = None
            for key, sess in self._sessions.items():
                if sess.id == session_id:
                    session_key = key
                    break

            if session_key:
                del self._sessions[session_key]
                logger.debug(f"Locally cleaned up session: {session_id}")
            else:
                logger.warning(f"Session {session_id} cannot be removed from local cache since this session was not found.")

    def session_details(self, session_key: str):
        """
        Retrieve details of a session by its key.
        """
        session = self._sessions.get(session_key)
        if session:
            print(dir(session))
            return {
                "id": session.id,
            }
        return None
