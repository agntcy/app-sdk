# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from agntcy_app_sdk.semantic.message import Message
from typing import Callable, Awaitable, AsyncIterator, List, Any, TypeVar, Type

"""
# Transport Mixins: Messaging Patterns & Unified Interface

             +--------------------+
             |     Transport      |
             |  (Unified API /    |
             |   shared state)    |
             +--------------------+
               ^       ^       ^
               |       |       |
        +------+   +---+-----+   +---------+
        | PointToPoint | FanOut | GroupChat|
        |   Mixin      | Mixin  |  Mixin   |
        +--------------+--------+----------+
         Direct 1→1    Broadcast    Multi-party
         messaging       1→N           chat

# - PointToPointMixin → Direct messaging 1→1
# - FanOutMixin       → Broadcast 1→N
# - GroupChatMixin    → Multi-party conversation
"""


# -----------------------------------------------------------------------------
# Point-to-Point Mixin
# -----------------------------------------------------------------------------
class PointToPointMixin(ABC):
    """
    Adds direct, one-to-one messaging capabilities to a transport.

    Expected use cases:
        - RPC-style request/response
        - Targeted messaging between two nodes
    Requires the concrete class to provide:
        - self._connection: the shared underlying transport/connection object
    """

    @abstractmethod
    async def send(self, recipient: str, message: Message, **kwargs) -> None:
        """
        Send a message to a single recipient without expecting a response.
        """
        raise NotImplementedError

    @abstractmethod
    async def request(self, recipient: str, message: Message, **kwargs) -> Message:
        """
        Send a message to a recipient and await a single response.
        """
        raise NotImplementedError

    @abstractmethod
    async def request_stream(
        self, recipient: str, message: Message, **kwargs
    ) -> AsyncIterator[Message]:
        """
        Send a request and receive a continuous stream of responses.

        Default implementation raises NotImplementedError. Concrete transports
        can override to provide streaming semantics.
        """
        raise NotImplementedError("Streaming not supported for point-to-point.")


# -----------------------------------------------------------------------------
# Fan-Out / Publish-Subscribe Mixin
# -----------------------------------------------------------------------------
class FanOutMixin(ABC):
    """
    Adds one-to-many messaging capabilities (publish/subscribe) to a transport.

    Expected use cases:
        - Broadcasting updates or notifications
        - Scatter/gather operations to multiple recipients
    Requires the concrete class to provide:
        - self._connection: the shared underlying transport/connection object
    """

    @abstractmethod
    async def publish(self, topic: str, message: Message, **kwargs) -> None:
        """
        Publish a message to all subscribers of the topic.
        """
        raise NotImplementedError

    @abstractmethod
    async def gather(self, topic: str, message: Message, **kwargs) -> List[Message]:
        """
        Publish a message and collect responses from multiple subscribers.

        Default implementation raises NotImplementedError. Concrete transports
        can override to provide scatter/gather semantics.
        """
        raise NotImplementedError("Gather not supported for this transport.")

    @abstractmethod
    async def gather_stream(
        self, topic: str, message: Message, **kwargs
    ) -> AsyncIterator[Message]:
        """
        Publish a message and yield responses from multiple subscribers.

        Default implementation raises NotImplementedError. Concrete transports
        can override to provide scatter/gather semantics.
        """
        raise NotImplementedError("Gather streaming not supported for this transport.")


# -----------------------------------------------------------------------------
# Group Chat / Multi-Party Conversation Mixin
# -----------------------------------------------------------------------------


class GroupChatMixin(ABC):
    """
    Adds multi-party group conversation capabilities to a transport.

    Expected use cases:
        - Group chat / collaborative channels
        - Streaming multi-party interactions
    Requires the concrete class to provide:
        - self._connection: the shared underlying transport/connection object
    """

    @abstractmethod
    async def start_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str,
        **kwargs,
    ) -> List[Message]:
        """
        Create a new conversation including the given participants.
        """
        raise NotImplementedError

    @abstractmethod
    async def start_streaming_conversation(
        self,
        group_channel: str,
        participants: List[str],
        init_message: Message,
        end_message: str,
        **kwargs,
    ) -> AsyncIterator[Message]:
        """
        Create a new streaming conversation including the given participants.
        """
        raise NotImplementedError


# -----------------------------------------------------------------------------
# Unified Transport Base Class
# -----------------------------------------------------------------------------

T = TypeVar("T", bound="BaseTransport")


class BaseTransport(PointToPointMixin, FanOutMixin, GroupChatMixin, ABC):
    """
    Unified messaging transport interface.

    This class combines multiple messaging patterns and optional streaming
    capabilities.

    Patterns included:
        - Point-to-Point (send/request/request_stream)
        - Fan-Out / Publish-Subscribe (publish/gather)
        - Group Chat / Conversations (create_conversation/get_conversation)
    """

    @classmethod
    @abstractmethod
    def from_client(cls: Type[T], client: Any, name: str = None) -> T:
        """Create a transport instance from a client."""
        pass

    @classmethod
    @abstractmethod
    def from_config(cls: Type[T], endpoint: str, name: str = None, **kwargs) -> T:
        """Create a transport instance from a configuration."""
        pass

    @abstractmethod
    def type(self) -> str:
        """Return the transport type."""
        pass

    @abstractmethod
    async def setup(self, **kwargs) -> None:
        """Perform any necessary setup for the transport, useful for async initialization."""
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close the transport connection."""
        pass

    # -----------------------------------------------------------------------------
    # Sserver-Side definitions
    # -----------------------------------------------------------------------------

    @abstractmethod
    async def subscribe(self, topic: str, callback: callable = None) -> None:
        """Subscribe to a topic with a callback."""
        pass

    @abstractmethod
    def set_callback(self, callback: Callable[..., Awaitable[Any]]) -> None:
        """Set the message handler function."""
        pass