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
from ..message import Message, Response

class RequestHandler(ABC):
    """Abstract base class for handling requests."""

    async def start(self) -> None:
        """Start the ASGI handler."""
        self._started = True
    
    @abstractmethod
    async def handle_incoming_request(self, message: Message) -> Response:
        """Handle a request message and return a response."""
        pass

    @abstractmethod
    def translate_outgoing_request(self, *args, **kwargs) -> Message:
        """Handle a request message and return a response."""
        pass