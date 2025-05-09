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

class BaseTransport(ABC):
    @abstractmethod
    def get(self, url, topic=None, params=None, **kwargs) -> Any:
        """Get a message from a topic."""
        pass

    @abstractmethod
    def post(self, url, topic=None, data=None, json=None, **kwargs) -> None:
        """Post a message to a topic."""
        pass
    
    @abstractmethod
    async def publish(self, org: str, namespace: str, topic: str, message: Any) -> None:
        """Publish a message to a topic."""
        pass

    @abstractmethod
    async def subscribe(self, org: str, namespace: str, topic: str, callback: callable) -> None:
        """Subscribe to a topic with a callback."""
        pass