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
from .message import Message, Response
from typing import Callable, Dict, Any, Optional
import asyncio

class BaseTransport(ABC):
    @abstractmethod
    def get_type(self) -> str:
        """Return the transport type."""
        pass

    @abstractmethod
    def bind_to_topic(self, topic: str) -> None:
        """Bind the transport to a specific topic. Will be used when no
        topic is specified in the request.
        """
        pass

    @abstractmethod
    async def get(
        self,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Any:
        """Get a message from a topic."""
        pass

    '''@abstractmethod
    async def post(
        self,
        url: str,
        *,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Any:
        """Post a message to a topic."""
        pass'''
    
    @abstractmethod
    async def set_message_translator(
        self, 
        translator: Callable[[dict], Message]
    ) -> None:
        """Set the message translator function."""
        pass
    
    @abstractmethod
    async def set_message_handler(
        self, 
        handler: Callable[[Message], asyncio.Future]
    ) -> None:
        """Set the message handler function."""
        pass
    
    @abstractmethod
    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        pass
    
    @abstractmethod
    async def send_response(self, response: Response) -> None:
        """Send a response message."""
        pass