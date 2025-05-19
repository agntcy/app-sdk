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
from typing import Any, Callable
from gateway_sdk.transports.transport import BaseTransport
from gateway_sdk.protocols.message import Message

class BaseAgentProtocol(ABC):
    """
    Base class for different agent protocols.
    """
    @abstractmethod
    def get_type(self) -> str:
        """Return the protocol type."""
        pass

    @abstractmethod
    def create_client(self, url: str = None, topic: str = None, transport: BaseTransport = None, **kwargs) -> Any:
        """Create a client for the protocol."""
        pass

    @abstractmethod
    def message_translator(self, request: Any) -> Message:
        """Translate a request into a message."""
        pass

    @abstractmethod
    def create_ingress_handler(self, *args, **kwargs) -> Callable[[Message], Message]:
        """Create an ingress handler for the protocol."""
        pass