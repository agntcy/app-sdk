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

from .base_transport import BaseTransport
from .adapters.base import RequestHandler
from .message import Message, Response
from .logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

class MessageBridge:
    """
    Bridge connecting message transport with request handlers.
    """
    def __init__(
        self,
        transport: BaseTransport,
        handler: RequestHandler,
        topic: str,
    ):
        self.transport = transport
        self.handler = handler
        self.topic = topic
    
    async def start(self):
        """Start all components of the bridge."""
        # Set up message handling flow
        self.transport.set_message_handler(self._process_message)
        
        # Start all components
        await self.handler.start()
        await self.transport.subscribe(self.topic)
        
        logger.info("Message bridge started.")
    
    async def _process_message(self, message: Message):
        """Process an incoming message through the handler and send response."""
        try:
            # Handle the request
            response = await self.handler.handle_incoming_request(message)
            
            # Send response if reply is expected
            if message.reply_to:
                await self.transport.send_response(response)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
            
            # Send error response if reply is expected
            if message.reply_to:
                error_response = Response(
                    status_code=500,
                    body={"error": str(e)},
                    correlation_id=message.reply_to
                )
                await self.transport.send_response(error_response)