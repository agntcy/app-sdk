# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any, Optional

from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.directory.base import BaseAgentDirectory


class ServerHandler(ABC):
    """Server-side handler. Owns server, optionally transport. Owns all transport wiring."""

    def __init__(
        self,
        server: Any,
        *,
        transport: Optional[BaseTransport] = None,
        topic: Optional[str] = None,
        directory: Optional[BaseAgentDirectory] = None,
    ):
        self._server = server
        self._transport = transport
        self._topic = topic
        self._directory = directory

    @property
    def transport(self) -> Optional[BaseTransport]:
        return self._transport

    @property
    def topic(self) -> Optional[str]:
        return self._topic

    @property
    def directory(self) -> Optional[BaseAgentDirectory]:
        return self._directory

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


class ClientFactory(ABC):
    """Standalone client factory — one per protocol. Pure client creation."""

    @abstractmethod
    def protocol_type(self) -> str:
        """Return the protocol type identifier."""
        ...

    @abstractmethod
    async def create_client(
        self,
        *,
        url: Optional[str] = None,
        topic: Optional[str] = None,
        transport: Optional[BaseTransport] = None,
        **kwargs: Any,
    ) -> Any:
        """Create a client for the protocol."""
        ...
