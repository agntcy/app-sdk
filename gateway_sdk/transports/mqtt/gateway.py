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
    def __init__(self, endpoint: str, client: Optional[mqtt.Client] = None):
        """
        Initialize the MQTT transport with the given endpoint and client.
        :param endpoint: The MQTT broker endpoint.
        :param client: An optional MQTT client instance. If not provided, a new one will be created.
        """

        self._hostname = endpoint.split(":")[0]
        self._port = int(endpoint.split(":")[1]) if ":" in endpoint else 1883
        self._callback = None

        # Enable the user to provide an instance of the MQTT client
        self._mqttc = client or mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

        self._mqttc.on_connect = self._on_connect
        self._mqttc.on_message = self._on_message

        if not self._mqttc:
            raise ValueError("MQTT client instance is required")
        
        if not self._mqttc.is_connected():
            # Connect to the MQTT broker
            logger.info(f"Connecting to MQTT broker at {endpoint}")
            self._mqttc.connect(self._hostname, self._port, 60)

        #raise NotImplementedError(
        #    "MQTT transport is not implemented yet. Please use NATS transport instead."
        #)

        self._mqttc.loop_forever()
    
    # The callback for when the client receives a CONNACK response from the server.
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            logger.error(f"Failed to connect: {reason_code}. loop_forever() will retry connection")
        if reason_code != mqtt.CONNACK_ACCEPTED:
            logger.error(f"Connection failed with code {reason_code}")
            return
        logger.info(f"Connected to MQTT broker at {self._hostname}:{self._port}")
        
        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        client.subscribe("$SYS/#")

    # The callback for when a PUBLISH message is received from the server.
    def _on_message(self, client, userdata, msg):
        logger.info(f"Received message: {msg.topic} {msg.payload}")

        if not self._callback:
            logger.warning("No callback set for incoming messages")
            return

    @staticmethod
    def type(self) -> str:
        """Return this transport type."""
        return "MQTT"

    async def close(self) -> None:
        """Close the MQTT connection."""
        if self._mqttc:
            self._mqttc.disconnect()
            logger.info("Disconnected from MQTT server")
        else:
            logger.warning("MQTT client is not connected")

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
        self.client.subscribe(topic)