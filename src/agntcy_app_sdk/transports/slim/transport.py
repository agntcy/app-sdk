# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional, Callable
import os
import slim_bindings
import asyncio
import inspect
import datetime
import uuid
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport, Message


configure_logging()
logger = get_logger(__name__)

"""
SLIM implementation of the BaseTransport interface.
"""


class SLIMTransport(BaseTransport):
    """
    SLIM Transport implementation using the slim_bindings library.
    """

    def __init__(
        self,
        client=None,
        endpoint: Optional[str] = None,
        default_org: str = "default",
        default_namespace: str = "default",
        message_timeout: datetime.timedelta = datetime.timedelta(seconds=10),
        message_retries: int = 2,
    ) -> None:
        self._endpoint = endpoint
        self._slim = client
        self._callback = None
        self._default_org = default_org
        self._default_namespace = default_namespace
        self.message_timeout = message_timeout
        self.message_retries = message_retries

        self._sessions = {}

        # Initialize identity provider/verifier with shared secret
        self._provider = slim_bindings.PyIdentityProvider.SharedSecret("test", "secret")
        self._verifier = slim_bindings.PyIdentityVerifier.SharedSecret("test", "secret")

        if os.environ.get("TRACING_ENABLED", "false").lower() == "true":
            # Initialize tracing if enabled
            from ioa_observe.sdk.instrumentations.slim import SLIMInstrumentor

            SLIMInstrumentor().instrument()
            logger.info("SLIMTransport initialized with tracing enabled")

        logger.info(f"SLIMTransport initialized with endpoint: {endpoint}")

    # ###################################################
    # BaseTransport interface methods
    # ###################################################

    @classmethod
    def from_client(cls, client, org="default", namespace="default") -> "SLIMTransport":
        # Optionally validate client
        return cls(client=client, default_org=org, default_namespace=namespace)

    @classmethod
    def from_config(
        cls, endpoint: str, org: str = "default", namespace: str = "default", **kwargs
    ) -> "SLIMTransport":
        """
        Create a SLIM transport instance from a configuration.
        :param endpoint: The SLIM server endpoint.
        :param org: The organization name.
        :param namespace: The namespace name.
        :param kwargs: Additional configuration parameters.
        """
        return cls(
            endpoint=endpoint, default_org=org, default_namespace=namespace, **kwargs
        )

    def type(self) -> str:
        """Return the transport type."""
        return "SLIM"

    async def close(self) -> None:
        pass

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set the message handler function."""
        self._callback = handler

    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Publish a message to a topic."""
        topic = self.santize_topic(topic)

        logger.info(f"Publishing {message.payload} to topic: {topic}")

        # if we are asked to provide a response, use or generate a reply_to topic
        if respond and not message.reply_to:
            message.reply_to = uuid.uuid4().hex
            print(f"Generated reply_to topic: {message.reply_to}")

        resp = await self._publish(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
            message=message,
            expected_responses=1 if respond else 0,
        )

        print(f"Published message to {topic} with response: {resp}")

        if respond:
            return resp[0] if resp else None


    async def subscribe(self, topic: str) -> None:
        """Subscribe to a topic with a callback."""
        topic = self.santize_topic(topic)

        await self._subscribe(
            org=self._default_org,
            namespace=self._default_namespace,
            topic=topic,
        )

        logger.info(
            f"Subscribed to {self._default_org}/{self._default_namespace}/{topic}"
        )

    # ###################################################
    # SLIM sub methods
    # ###################################################

    async def _subscribe(self, org: str, namespace: str, topic: str) -> None:
        print(f"Dummy subscribe called for {org}.{namespace}.{topic}")
        pass

    async def _publish(
        self,
        org: str,
        namespace: str,
        topic: str,
        message: Message,
        expected_responses: int = 0,
    ) -> None:
        """
        Sends a message to the SLIM receiver and returns the response.
        
        Args:
            org: Organization name
            namespace: Namespace name
            topic: Topic/receiver name (acts as receiver)
            message: Message to send
            expected_responses: Number of expected responses
            
        Returns:
            list: List of responses from receivers
            
        Raises:
            RuntimeError: If communication with the receiver fails
        """
        logger.info(f"Connecting to SLIM server for receiver: {org}.{namespace}.{topic}")
        
        # Create sender and receiver names (topic is the receiver)
        sender = slim_bindings.PyName(org, namespace, "sender")
        receiver = slim_bindings.PyName(org, namespace, topic)
        
        # Create and connect SLIM
        slim = await slim_bindings.Slim.new(sender, self._provider, self._verifier)
        
        try:
            async with slim:
                await slim.connect({"endpoint": "http://localhost:46357", "tls": {"insecure": True}})
                await slim.subscribe(sender)
                
                # Create session and set route
                session = await slim.create_session(slim_bindings.PySessionConfiguration.FireAndForget())
                await slim.set_route(receiver)
                
                # Give receiver time to be ready
                await asyncio.sleep(1)
                
                logger.info("Connected to SLIM server")
                
                # Send message directly
                logger.info(f"Sending message to receiver: {message}")
                
                responses = []
                if expected_responses > 0:
                    _, reply = await slim.request_reply(
                        session,
                        message.payload.encode(),
                        receiver,
                        timeout=datetime.timedelta(seconds=10)
                    )
                    response_text = reply.decode()
                    logger.info(f"Received response: {response_text}")
                    
                    # Create response message
                    response_msg = Message(payload=response_text)
                    responses.append(response_msg)
                
                return responses
        except Exception as e:
            logger.error(f"Failed to send message via SLIM: {e}")
            raise RuntimeError(f"Failed to communicate with receiver: {e}")


    async def receive_back(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Receive a message from a topic."""
        print(f"Dummy receive_back called for {topic}")
        pass

    def santize_topic(self, topic: str) -> str:
        """Sanitize the topic name to ensure it is valid for NATS."""
        # NATS topics should not contain spaces or special characters
        sanitized_topic = topic.replace(" ", "_")
        return sanitized_topic