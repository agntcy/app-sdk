# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.common.logging_config import get_logger
from typing import Callable
import asyncio

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

    async def start(self, blocking: bool = False):
        """Start all components of the bridge."""
        # Set up message handling flow
        self.transport.set_callback(self._process_message)

        # Start all components
        await self.transport.subscribe(self.topic)

        logger.info("Message bridge started.")

        if blocking:
            # Run the loop forever if blocking is True
            await self.loop_forever()

    async def loop_forever(self):
        """Run the bridge indefinitely."""
        logger.info("Message bridge is running. Waiting for messages...")
        while True:
            try:
                # Wait for messages to be processed
                await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                logger.info("Message bridge loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in message bridge loop: {e}")

    async def _process_message(self, message: Message):
        """Process an incoming message through the handler and send response."""
        try:
            # Handle the request
            response = await self.handler(message)

            if not response:
                logger.warning("Handler returned no response for message.")
                return

            # Send response if reply is expected
            if message.reply_to:
                response.reply_to = message.reply_to

                # Send the response back through the transport using publish
                await self.transport.publish(
                    topic=response.reply_to,
                    message=response,
                    respond=False,
                )
            else:
                return response

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            # Send error response if reply is expected
            if message.reply_to:
                error_response = Message(
                    type="error", payload=str(e), reply_to=message.reply_to
                )
                await self.transport.publish(
                    topic=message.reply_to,
                    message=error_response,
                    respond=False,
                )
