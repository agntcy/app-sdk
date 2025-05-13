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
import nats
from ..base_transport import BaseTransport
from ..logging_config import configure_logging, get_logger
from ..message import Message, Response
from typing import Callable, Dict, Any, Optional
import json

configure_logging()
logger = get_logger(__name__)

"""
Nats implementation of BaseTransport.
"""

class NatsGateway(BaseTransport):
    def __init__(self, endpoint: str, auth=None):
        self.type = "NATS"
        self._nc = None
        self.endpoint = endpoint
        self.auth = auth
        self._handler = None
        self._default_topic = None
        self._message_translator = None
        self.subscriptions = []

    def get_type(self) -> str:
        return self.type

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_").replace(".", "_")
        return sanitized_topic
        
    async def _connect(self):
        self._nc = await nats.connect(self.endpoint)
        logger.info("Connected to NATS server")

    def bind_to_topic(self, topic: str) -> None:
        """Bind the transport to a specific topic. Will be used when no
        topic is specified in the request.
        """
        self._default_topic = topic

    def set_message_translator(
        self,
        translator: Callable[[dict], Message]
    ) -> None:
        """Set the message translator function."""
        self._message_translator = translator

    def set_message_handler(
        self, 
        handler: Callable[[Message], asyncio.Future]
    ) -> None:
        """Set the message handler function."""
        self._handler = handler

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
        if not self._message_translator:
            raise ValueError("Message translator must be set if using get()")
        
        if not self._default_topic:
            # TODO: use the message body to get the topic
            raise ValueError("No default topic set for NATS transport")
        
        if self._nc is None:
            await self._connect()

        req = self._message_translator(
            {
                "method": "GET",
                "url": url,
                "params": params,
                "headers": headers,
                "correlation_id": correlation_id,
                "reply_to": reply_to
            }
        )

        resp = await self._nc.request(
            self.santize_topic(self._default_topic),
            json.dumps(req.to_dict()).encode(),
            timeout=5 # TODO: make this configurable
        )

        return self._message_handler(resp)

    async def subscribe(self, topic: str) -> None:
        topic = self.santize_topic(topic)

        """Subscribe to a topic with a callback."""
        if self._nc is None:
            await self._connect()
        
        if not self._handler:
            raise ValueError("Message handler must be set before starting transport")
        
        sub = await self._nc.subscribe(topic, cb=self._message_handler)
        self.subscriptions.append(sub)
        logger.info(f"Subscribed to topic: {topic}")

    async def _message_handler(self, nats_msg):
        """Internal handler for NATS messages."""
        try:
            # Decode the message payload
            payload = json.loads(nats_msg.data.decode())
            
            # Create a Message object
            message = Message.from_dict(payload)
            
            # Add reply_to from NATS message if not in payload
            if nats_msg.reply and not message.reply_to:
                message.reply_to = nats_msg.reply
                
            # Process the message with the registered handler
            if self._handler:
                await self._handler(message)
                
        except json.JSONDecodeError:
            print("Error: Message payload is not valid JSON")
        except Exception as e:
            print(f"Error handling NATS message: {str(e)}")
    
    async def send_response(self, response: Response) -> None:
        """Send a response via NATS."""
        logger.info(f"Sending response {response.status_code} via NATS")

        if not response.correlation_id:
            logger.warning("no correlation_id in response, can't route back")
            return
            
        try:
            response_data = response.to_dict()
            await self._nc.publish(
                response.correlation_id,
                json.dumps(response_data).encode()
            )
            print(f"Response sent to {response.correlation_id}")
        except Exception as e:
            print(f"Error sending NATS response: {str(e)}")
