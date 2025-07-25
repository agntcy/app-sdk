# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any, Callable
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message


class BaseAgentProtocol(ABC):
    """
    Base class for different agent protocols.
    """

    @abstractmethod
    def type(self) -> str:
        """Return the protocol type."""
        pass

    @abstractmethod
    def create_client(
        self,
        url: str = None,
        topic: str = None,
        transport: BaseTransport = None,
        **kwargs,
    ) -> Any:
        """Create a client for the protocol."""
        pass

    @abstractmethod
    def message_translator(
        self, request: Any, headers: dict[str, Any] | None = None
    ) -> Message:
        """Translate a request into a message."""
        pass

    @abstractmethod
    def create_ingress_handler(self, *args, **kwargs) -> Callable[[Message], Message]:
        """Create an ingress handler for the protocol."""
        pass
