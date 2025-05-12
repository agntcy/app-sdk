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
from typing import Any
from .base_transport import BaseTransport

class BaseProtocol(ABC):
    """
    Base class for different agent protocols.
    """
    @abstractmethod
    def get_type(self) -> str:
        """Return the protocol type."""
        pass

    @abstractmethod
    def create_client(self, url: str, transport: BaseTransport = None, auth: Any = None) -> Any:
        """Create a client for the protocol."""
        pass

    @abstractmethod
    def create_receiver(self) -> Any:
        """Create a receiver for the protocol."""
        pass