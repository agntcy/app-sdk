# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message


class BaseAgentProtocolHandler(ABC):
    """
    Base class for different agent protocols.
    """

    @abstractmethod
    def type(self) -> str:
        """Return the protocol type."""
        pass

    @abstractmethod
    def agent_record(self):
        """
        Return the identifying record for this agent handler.
        """
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
    def create_agent_topic(*args, **kwargs) -> str:
        """Create a unique topic identifier for the agent."""
        pass

    @abstractmethod
    def bind_server(self, server: Any) -> None:
        """Bind the protocol to a server."""
        pass

    @abstractmethod
    def setup_ingress_handler(self, *args, **kwargs) -> None:
        """Create an ingress handler for the protocol."""
        pass

    @abstractmethod
    def handle_message(self, message: Message) -> Message:
        """Handle an incoming message and return a response."""
        pass
