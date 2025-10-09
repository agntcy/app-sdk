# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.protocol import BaseAgentProtocolHandler
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.discovery.directory import BaseAgentDirectory
from agntcy_app_sdk.common.logging_config import get_logger
import asyncio
import inspect

logger = get_logger(__name__)


class MessageBridge:
    """
    Bridge connecting message transport with request handlers.
    """

    def __init__(
        self,
        transport: BaseTransport,
        protocol_handler: BaseAgentProtocolHandler,
        topic: str,
        agent_directory: BaseAgentDirectory,
    ):
        self.transport = transport
        self.protocol_handler = protocol_handler
        self.topic = topic
        self.agent_directory = agent_directory

    async def start(self, blocking: bool = False, publish_to_directory: bool = False):
        """Start all components of the bridge."""

        # set the message handler to the protocol handler's handle_message method
        self.handler = self.protocol_handler.handle_message

        # Set up the transport layer (this starts the listener task)
        await self.transport.setup()

        # Set callback AFTER transport setup
        self.transport.set_callback(self._process_message)

        await self.transport.subscribe(self.topic)

        # check if protocol_handler.setup_ingress_handler is async or sync
        if inspect.iscoroutinefunction(self.protocol_handler.setup_ingress_handler):
            await self.protocol_handler.setup_ingress_handler()
        else:
            self.protocol_handler.setup_ingress_handler()

        # signal if we should push this protocol's agent record to the directory
        if publish_to_directory:
            await self.agent_directory.publish_agent_record(
                self.protocol_handler.agent_record()
            )

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
            if inspect.iscoroutinefunction(self.handler):
                response = await self.handler(message)
            else:
                result = self.handler(message)
                # If the result is a coroutine, await it
                if inspect.iscoroutine(result):
                    response = await result
                else:
                    response = result

            if not response:
                logger.warning("Handler returned no response for message.")

            return response

        except Exception as e:
            logger.error(f"Error processing message: {e}")
            return None
