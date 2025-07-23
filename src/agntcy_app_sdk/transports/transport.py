# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from agntcy_app_sdk.protocols.message import Message
from typing import Callable, Optional
from typing import Any, TypeVar, Type
import asyncio

T = TypeVar("T", bound="BaseTransport")


class BaseTransport(ABC):
    """
    Abstract base class for transport protocols.
    This class defines the interface for different transport protocols
    such as AGP, NATS, MQTT, etc.
    """

    @classmethod
    @abstractmethod
    def from_client(cls: Type[T], client: Any) -> T:
        """Create a transport instance from a client."""
        pass

    @classmethod
    @abstractmethod
    def from_config(cls: Type[T], endpoint: str, **kwargs) -> T:
        """Create a transport instance from a configuration."""
        pass

    @abstractmethod
    def type(self) -> str:
        """Return the transport type."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""
        pass

    @abstractmethod
    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        pass

    @abstractmethod
    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Publish a message to a topic."""
        pass

    @abstractmethod
    async def subscribe(self, topic: str, callback: callable = None) -> None:
        """Subscribe to a topic with a callback."""
        pass

    @abstractmethod
    async def broadcast(
        self,
        topic: str,
        message: Message,
        expected_responses: int = 1,
        timeout: Optional[float] = 30.0,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        pass
