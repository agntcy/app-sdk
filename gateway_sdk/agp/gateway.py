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

from typing import Any, Optional
import agp_bindings
from agp_bindings import GatewayConfig
import asyncio
import inspect
import json
import base64
from ..logging_config import configure_logging, get_logger
from ..base_transport import BaseTransport

configure_logging()
logger = get_logger(__name__)

"""
Implementations of the BaseGateway class for different protocols.
These classes should implement the abstract methods defined in BaseGateway.
"""

class Message:
    def __init__(
        self,
        subject: str,
        data: bytes,
        reply: Optional[str] = None,
        headers: Optional[dict[str, str]] = None,
    ):
        self.subject = subject         # Topic or subject of the message
        self.data = data               # Raw payload
        self.reply = reply             # Optional reply subject
        self.headers = headers or {}   # Optional headers

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "data": base64.b64encode(self.data).decode("utf-8"),
            "reply": self.reply,
            "headers": self.headers,
        }

    def to_bytes(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes) -> "Message":
        obj = json.loads(raw.decode("utf-8"))
        return cls(
            subject=obj["subject"],
            data=base64.b64decode(obj["data"]),
            reply=obj.get("reply"),
            headers=obj.get("headers"),
        )

    def __repr__(self):
        return (
            f"<Message subject='{self.subject}' reply='{self.reply}' "
            f"headers={self.headers} data={self.data[:50]}...>"
        )

class AGPGateway(BaseTransport):
    """
    AGP Gateway implementation using the agp_bindings library.
    """
    def __init__(self, endpoint: str, auth: None):
        self.endpoint = endpoint
        self.gateway = None

    def get(self, *args: Any, **kwargs: Any) -> None:
        """
        This method is not implemented for AGP Gateway.
        """
        raise NotImplementedError("AGP Gateway does not support GET requests.")
    
    def post(self, *args: Any, **kwargs: Any) -> None:  
        """
        This method is not implemented for AGP Gateway.
        """
        raise NotImplementedError("AGP Gateway does not support POST requests.")

    async def _create_gateway(self, org: str, namespace: str, topic: str) -> None:
        # create new gateway object
        self.gateway = await agp_bindings.Gateway.new(org, namespace, topic)

        # Configure gateway
        config = GatewayConfig(endpoint=self.endpoint, insecure=True)
        self.gateway.configure(config)

        # Connect to remote gateway server
        _ = await self.gateway.connect()

        logger.info(f"connected to gateway @{self.endpoint}")

    async def subscribe(self, org: str, namespace: str, topic: str, callback: callable) -> None:
        await self._create_gateway(org, namespace, topic)

        async with self.gateway:
            # Wait for a message and reply in a loop
            while True:
                session_info, _ = await self.gateway.receive()

                async def background_task(session_id):
                    while True:
                        # Receive the message from the session
                        session, msg = await self.gateway.receive(session=session_id)

                        # load the message and determine if its a reply
                        if msg:
                            msg = Message.from_bytes(msg)
                            logger.info(f"Received message: {msg}")

                            if inspect.iscoroutinefunction(callback):
                                output = await callback(msg.data.decode())
                            else:
                                output = callback(msg.data.decode())

                            if msg.reply:
                                payload = json.dumps(output).encode("utf-8")
                                await self.gateway.publish_to(session, payload)

                asyncio.create_task(background_task(session_info.id))

    async def publish(self, org: str, namespace: str, topic: str, message: bytes) -> None:
        if not self.gateway:
            # TODO: create a hash for the topic so its private since we havnt run subscribe
            await self._create_gateway("default", "default", "default")

        async with self.gateway:
            # Create a route to the remote ID
            await self.gateway.set_route(org, namespace, topic)

            # create a session
            session = await self.gateway.create_ff_session(agp_bindings.PyFireAndForgetConfiguration())

            msg = Message(
                subject=topic,
                data=message,
                reply=None,
                headers=None,
            )

            # Send the message
            await self.gateway.publish(
                session,
                msg.to_bytes(),
                org,    
                namespace,
                topic,
            )

    async def request(self, org: str, namespace: str, topic: str, message: bytes) -> None:
        if not self.gateway:
            # TODO: create a hash for the topic so its private since we havnt run subscribe
            await self._create_gateway("default", "default", "default")

        async with self.gateway:
            # Create a route to the remote ID
            await self.gateway.set_route(org, namespace, topic)

            # create a session
            session = await self.gateway.create_ff_session(agp_bindings.PyFireAndForgetConfiguration())

            msg = Message(
                subject=topic,
                data=message,
                reply=True,
                headers=None,
            )

            # Send the message
            await self.gateway.publish(
                session,
                msg.to_bytes(),
                org,    
                namespace,
                topic,
            )

            session_info, msg = await self.gateway.receive(session=session.id)
            logger.debug(f"Received message: {msg.decode()}")
            return msg.decode()

