# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any, Optional

from agntcy_app_sdk.transport.base import BaseTransport


class ServerHandler(ABC):
    """Server-side handler. Owns managed object, optionally transport. Owns all transport wiring."""

    def __init__(
        self,
        managed_object: Any,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
    ):
        self._managed_object = managed_object
        self._transport = transport
        self._topic = topic

    @property
    def transport(self) -> Optional[BaseTransport]:
        return self._transport

    @property
    def topic(self) -> Optional[str]:
        return self._topic

    def get_agent_record(self) -> Optional[Any]:
        """Return the record to push to the agent directory, or ``None``.

        Subclasses that publish to a directory (e.g. A2A handlers with an
        ``AgentCard``) should override this method.
        """
        return None

    @abstractmethod
    def protocol_type(self) -> str:
        """Return the protocol type identifier."""
        ...

    @abstractmethod
    async def setup(self) -> None:
        """Full lifecycle: transport.setup(), set_callback(), subscribe(), protocol init."""
        ...

    @abstractmethod
    async def teardown(self) -> None:
        """Close transport, cancel tasks."""
        ...
