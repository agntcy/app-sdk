# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

from abc import ABC, abstractmethod
from agntcy_app_sdk.protocols.message import Message
from typing import Callable, Dict, Optional
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
        headers: Optional[Dict[str, str]] = None,
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
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        pass
