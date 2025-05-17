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

import asyncio
import paho.mqtt.client as mqtt
from gateway_sdk.transports.transport import BaseTransport
from gateway_sdk.common.logging_config import configure_logging, get_logger
from gateway_sdk.protocols.message import Message
from typing import Callable, Dict, Optional
from opentelemetry.propagate import inject, extract
from opentelemetry import trace

configure_logging()
logger = get_logger(__name__)

"""
MQTT implementation of BaseTransport.
"""

class MQTTGateway(BaseTransport):
    def __init__(self, endpoint: str, *args, **kwargs):
        self.endpoint = endpoint
        self._callback = None

        self._mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        raise NotImplementedError(
            "MQTT transport is not implemented yet. Please use NATS transport instead."
        )

    @staticmethod
    def type(self) -> str:
        """Return this transport type."""
        return "MQTT"

    async def close(self) -> None:
        pass

    def set_callback(
        self, 
        callback: Callable[[Message], asyncio.Future]
    ) -> None:
        """Set the message handler function."""
        self._callback = callback

    async def publish(
        self, 
        topic: str, 
        message: Message, 
        respond: Optional[bool] = False,
        headers: Optional[Dict[str, str]] = None
    ) -> None:
        """Publish a message to a topic."""
        pass

    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        pass