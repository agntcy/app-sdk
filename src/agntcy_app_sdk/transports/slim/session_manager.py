# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Dict
import datetime
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
import slim_bindings
from slim_bindings import (
    PyName,
    PySessionInfo,
    PySessionConfiguration,
    PySessionDirection,
)
from threading import Lock

configure_logging()
logger = get_logger(__name__)


class SessionManager:
    def __init__(self):
        self._sessions: Dict[str, PySessionInfo] = {}
        self._slim = None
        self._lock = Lock()

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
                logger.warning(
                    f"Error retrieving SLIM session config for {session_id}: {e}"
                )
                continue

        with self._lock:
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
        timeout: datetime.timedelta = datetime.timedelta(seconds=30),
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
        with self._lock:
            if session_key in self._sessions:
                logger.info(f"Reusing existing group broadcast session: {session_key}")
                return session_key, self._sessions[session_key]

        logger.info(f"Creating new group broadcast session: {session_key}")
        with self._lock:
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
                logger.info(f"Inviting {invitee} to session {session_info.id}")
                await self._slim.set_route(invitee)
                await self._slim.invite(session_info, invitee)

            # store the session info
            self._sessions[session_key] = session_info
            return session_key, session_info

    async def close_session(self, session_id: int):
        """
        Close and remove a session by its key.
        """
        if not self._slim:
            raise ValueError("SLIM client is not set")

        try:
            await self._slim.delete_session(session_id)
            logger.debug(f"Closed session: {session_id}")

            with self._lock:
                # Remove session from the manager's dictionary
                session_key = None
                for key, sess in self._sessions.items():
                    if sess.id == session_id:
                        session_key = key
                        break

                if session_key:
                    del self._sessions[session_key]
        except Exception as e:
            logger.warning(f"Error closing SLIM session {session_id}: {e}")
            return

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
