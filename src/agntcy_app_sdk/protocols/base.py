# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from typing import Any
from agntcy_app_sdk.transports.base import BaseTransport
from agntcy_app_sdk.directory.base import BaseAgentDirectory
from agntcy_app_sdk.protocols.message import Message


class BaseAgentSemanticLayer(ABC):
    """
    Base class for agentic semantic protocols A2A|MCP|AP, focusing on
    the translation and transformation of types.
    """

    @abstractmethod
    def type(self) -> str:
        """Return the protocol type."""
        pass

    @abstractmethod
    def create_agent_topic(*args, **kwargs) -> str:
        """Standard way to create a topic identifier for the agent."""
        pass

    @abstractmethod
    def get_agent_record(self):
        """Return the identifying record for this semantic type."""
        pass

    @abstractmethod
    def serialize_agent_record(self, record: Any = None) -> bytes:
        """Serialize this agent record"""
        # can pass in a record or use instance record
        pass

    @abstractmethod
    def deserialize_agent_record(self, record: bytes) -> Any:
        """Deserialize this agent record"""
        pass

    @abstractmethod
    def to_base_message(self, *args, **kwargs) -> Message:
        """Translate a request into a message."""
        pass

    @abstractmethod
    def to_semantic_type(self, type: str) -> Any:
        """Translate this record type to another semantic type."""
        pass


class BaseAgentSemanticServiceHandler(BaseAgentSemanticLayer):
    """
    Base class for service-level semantic agent protocol translations.
    Protocols likely have a schema and service.
    """

    @abstractmethod
    def create_client(
        self,
        url: str = None,
        topic: str = None,
        transport: BaseTransport = None,
        **kwargs,
    ) -> Any:
        """Create a client for this semantic protocol."""
        pass

    @abstractmethod
    def create_client_from_record(
        self,
        record: Any,
        record_ref: str = None,
        transport: BaseTransport = None,
        directory: BaseAgentDirectory = None,
        **kwargs,
    ):
        """Create a client from a record or record ref if directory is passed"""
        pass

    @abstractmethod
    def process_message_callback(self, message: Message) -> Message:
        """Process an incoming message, will be called by an app handler/message bridge"""
        pass


"""
Backwards compatible abstract class.
"""


class BaseAgentProtocolHandler(ABC):
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
