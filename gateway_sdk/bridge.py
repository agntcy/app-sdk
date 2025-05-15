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

from gateway_sdk.transports.base_transport import BaseTransport
from gateway_sdk.protocols.message import Message
from gateway_sdk.common.logging_config import configure_logging, get_logger
from typing import Callable

configure_logging()
logger = get_logger(__name__)

class MessageBridge:
    """
    Bridge connecting message transport with request handlers.
    """
    def __init__(
        self,
        transport: BaseTransport,
        handler: Callable[[Message], Message],
        topic: str,
    ):
        self.transport = transport
        self.handler = handler
        self.topic = topic
    
    async def start(self):
        """Start all components of the bridge."""
        # Set up message handling flow
        self.transport.set_callback(self._process_message)
        
        # Start all components
        await self.transport.subscribe(self.topic)
        
        logger.info("Message bridge started.")
    
    async def _process_message(self, message: Message):
        """Process an incoming message through the handler and send response."""
        try:
            # Handle the request
            response = await self.handler(message)
            
            # Send response if reply is expected
            if message.reply_to:
                response.reply_to = message.reply_to
                await self.transport.send_response(response)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Send error response if reply is expected
            if message.reply_to:
                error_response = Message(
                    type="error",
                    payload=str(e),
                    reply_to=message.reply_to
                )
                await self.transport.send_response(error_response)