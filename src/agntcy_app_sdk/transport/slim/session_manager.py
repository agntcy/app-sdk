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
    PySessionInfo,
    PySessionConfiguration,
    PySessionDirection,
)
from agntcy_app_sdk.semantic.message import Message

configure_logging()
logger = get_logger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, PySessionInfo] = {}
        self._slim = None
        self._lock = asyncio.Lock()

    def set_slim(self, slim: slim_bindings.Slim):
        """
        Set the SLIM client instance for the session manager.
        """
        self._slim = slim

    async def request_reply_session(
        self,
        max_retries: int = 5,
        timeout: datetime.timedelta = datetime.timedelta(seconds=5),
        mls_enabled: bool = True,
    ):
        """
        Create a new request-reply session with predefined configuration.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        # check if we already have a request-reply session
        for session_id, (session, q) in self._slim.sessions.items():
            try:
                conf = await self._slim.get_session_config(session_id)
                # compare the type of conf to PySessionConfiguration.FireAndForget
                if isinstance(conf, PySessionConfiguration.FireAndForget):
                    return session_id, session
            except Exception as e:
                # TODO: Revisit with SLIM team if this still exists in 0.5.0
                logger.debug(
                    f"could not retrieve SLIM session config for {session_id}: {e}"
                )
                continue

        async with self._lock:
            session = await self._slim.create_session(
                PySessionConfiguration.FireAndForget(
                    max_retries=max_retries,
                    timeout=timeout,
                    sticky=True,
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
        session_key = f"PySessionConfiguration.Streaming:{channel}:" + ",".join(
            [str(invitee) for invitee in invitees]
        )
        # use the same lock for session creation and lookup
        async with self._lock:
            if session_key in self._sessions:
                logger.info(f"Reusing existing group broadcast session: {session_key}")
                return session_key, self._sessions[session_key]

            logger.debug(f"Creating new group broadcast session: {session_key}")
            session_info = await self._slim.create_session(
                PySessionConfiguration.Streaming(
                    PySessionDirection.BIDIRECTIONAL,
                    topic=channel,
                    moderator=True,
                    max_retries=max_retries,
                    timeout=timeout,
                    mls_enabled=mls_enabled,
                )
            )

            for invitee in invitees:
                try:
                    logger.debug(f"Inviting {invitee} to session {session_info.id}")
                    await self._slim.set_route(invitee)
                    await self._slim.invite(session_info, invitee)
                except Exception as e:
                    logger.error(f"Failed to invite {invitee}: {e}")

            # store the session info
            self._sessions[session_key] = session_info
            return session_key, session_info

    async def close_session(
        self, session: PySessionInfo, remote: PyName = None, end_signal: str = None
    ):
        """
        Close and remove a session by its key.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        try:
            # send the end signal to the remote if provided
            if remote is not None and end_signal is not None:
                logger.info(f"Sending end signal '{end_signal}' to remote {remote}")

                end_msg = Message(
                    type="text/plain",
                    headers={"x-session-end-message": end_signal},
                    payload=end_signal,
                )
                await self._slim.publish(session, end_msg.serialize(), remote)

            logger.info(f"waiting before closing session: {session.id}")
            # todo: proper way to wait for all messages to be processed
            await asyncio.sleep(
                random.uniform(5, 10)
            )  # add sleep before closing to allow for any in-flight messages to be processed
            logger.info(f"deleting session: {session.id}")

            # Sometimes SLIM delete_session can hang indefinitely but still deletes the session, so we add a timeout
            try:
                await asyncio.wait_for(
                    self._slim.delete_session(session.id), timeout=5.0
                )
                logger.info(
                    f"Session {session.id} deleted successfully within timeout."
                )
            except asyncio.TimeoutError:
                logger.info(f"Timed out while trying to delete session {session.id}.")
            except Exception as e:
                logger.error(f"Error deleting session {session.id}: {e}")
            logger.info(f"Closed session: {session.id}")

            # remove from local store
            await self._local_cache_cleanup(session.id)
        except Exception as e:
            logger.warning(f"Error closing SLIM session {session.id}: {e}")
            return

    async def _local_cache_cleanup(self, session_id: int):
        """
        Perform local cleanup of a session without attempting to close it on the SLIM client.
        """
        async with self._lock:
            session_key = None
            for key, sess in self._sessions.items():
                if sess.id == session_id:
                    session_key = key
                    break

            if session_key:
                del self._sessions[session_key]
                logger.debug(f"Locally cleaned up session: {session_id}")

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
