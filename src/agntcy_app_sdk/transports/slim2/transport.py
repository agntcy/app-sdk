# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import datetime
import logging
import uuid
from typing import Optional, List, Dict, Any
import slim_bindings

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message

configure_logging()
logger = get_logger(__name__)
print("SLIM2Transport initialized new one")

class SLIM2Transport(BaseTransport):
    """
    A2A over SLIM 2.0 Transport
    
    Production-ready transport that bridges A2A protocol over SLIM v0.4.0
    with robust error handling, session management, and performance optimizations.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:46357",
        org: str = "default",
        namespace: str = "default",
        identity: str = "agent",
        shared_secret: str = "test-shared-secret-123",
        connection_timeout: int = 10,
        request_timeout: int = 30,
        max_retries: int = 3,
    ):
        """
        Initialize SLIM2 Transport.
        
        Args:
            endpoint: SLIM server endpoint
            org: Organization namespace  
            namespace: Service namespace
            identity: Agent identity name
            shared_secret: Authentication secret
            connection_timeout: Connection timeout in seconds
            request_timeout: Request timeout in seconds
            max_retries: Maximum connection retry attempts
        """
        self._endpoint = endpoint
        self._org = org
        self._namespace = namespace
        self._identity = identity
        self._shared_secret = shared_secret
        self._connection_timeout = connection_timeout
        self._request_timeout = request_timeout
        self._max_retries = max_retries
        
        # Connection management
        self._slim_connection = None
        self._connection_lock = asyncio.Lock()
        self._is_connected = False
        
        # Session management
        self._active_sessions: Dict[str, Any] = {}
        self._session_lock = asyncio.Lock()
        
        # Callback for incoming messages
        self._callback = None
        
        # Authentication setup
        self._provider = slim_bindings.PyIdentityProvider.SharedSecret(
            self._identity, self._shared_secret
        )
        self._verifier = slim_bindings.PyIdentityVerifier.SharedSecret(
            self._identity, self._shared_secret
        )
        
        logger.info(f"SLIM2Transport initialized: {endpoint} ({org}/{namespace}/{identity})")

    @classmethod
    def from_client(cls, client: Any) -> "SLIM2Transport":
        """Create transport from existing SLIM client."""
        logger.info(f"from_client() called with client: {type(client)}")
        # For compatibility - not implemented as we manage connection internally
        logger.warning("from_client() not supported - SLIM2Transport manages its own connections")
        raise NotImplementedError("SLIM2Transport manages its own connections")

    @classmethod
    def from_config(cls, endpoint: str, **kwargs) -> "SLIM2Transport":
        """Create transport from configuration."""
        logger.info(f"from_config() called with endpoint: {endpoint}, kwargs: {kwargs}")
        instance = cls(endpoint=endpoint, **kwargs)
        logger.info(f"Created SLIM2Transport from config: {endpoint}")
        return instance

    def type(self) -> str:
        """Return transport type."""
        logger.info("type() called - returning 'SLIM2'")
        return "SLIM2"

    async def close(self) -> None:
        """Close transport connection and cleanup resources."""
        logger.info("close() - SLIM2Transport uses per-request connections, no cleanup needed")
        pass

    def set_callback(self, handler) -> None:
        """Set message handler callback."""
        logger.info(f"set_callback() - Setting message handler: {type(handler)}")
        self._callback = handler
        logger.info(f"set_callback() - Callback set successfully, handler: {handler}")

    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> Optional[Message]:
        """Publish message to topic using SLIM request-reply."""
        topic = self._sanitize_topic(topic)
        logger.info(f"[PUBLISH] Starting → {self._org}/{self._namespace}/{topic}")
        
        # Create SLIM instance and connect
        sender_name = slim_bindings.PyName(self._org, self._namespace, self._identity)
        receiver_name = slim_bindings.PyName(self._org, self._namespace, topic)
        slim = await slim_bindings.Slim.new(sender_name, self._provider, self._verifier)
        
        async with slim:
            logger.info(f"[PUBLISH] Connecting to SLIM: {self._endpoint}")
            await slim.connect({"endpoint": self._endpoint, "tls": {"insecure": True}})
            await slim.subscribe(sender_name)
            
            logger.info(f"[PUBLISH] Creating session and setting route")
            session = await slim.create_session(slim_bindings.PySessionConfiguration.FireAndForget())
            await slim.set_route(receiver_name)
            await asyncio.sleep(0.1)  # Brief routing wait
            
            # Send and get response
            logger.info(f"[PUBLISH] Sending request → {topic}")
            timeout = datetime.timedelta(seconds=self._request_timeout)
            _, reply_bytes = await slim.request_reply(session, message.serialize(), receiver_name, timeout=timeout)
            
            response_text = reply_bytes.decode()
            logger.info(f"[PUBLISH] Got response ← {topic} ({len(response_text)} chars)")
            try:
                response = Message.deserialize(response_text)
                logger.info(f"[PUBLISH] Success ✓ {self._org}/{self._namespace}/{topic}")
                return response
            except Exception:
                logger.info(f"[PUBLISH] Fallback response ← {topic}")
                return Message(payload=response_text)

    async def subscribe(self, topic: str, callback=None) -> None:
        """Subscribe to topic for incoming messages."""
        handler = callback or self._callback
        if not handler:
            logger.error(f"No callback handler for topic {topic}")
            return
            
        topic = self._sanitize_topic(topic)
        logger.info(f"[SUBSCRIBE] Starting ← {self._org}/{self._namespace}/{topic}")
        
        # Create SLIM instance and connect
        receiver_name = slim_bindings.PyName(self._org, self._namespace, topic)
        slim = await slim_bindings.Slim.new(receiver_name, self._provider, self._verifier)
        
        async def simple_receive():
            async with slim:
                logger.info(f"[SUBSCRIBE] Connecting to SLIM: {self._endpoint}")
                await slim.connect({"endpoint": self._endpoint, "tls": {"insecure": True}})
                await slim.subscribe(receiver_name)
                logger.info(f"[SUBSCRIBE] Ready ← {topic}")
                
                while True:
                    logger.info(f"[SUBSCRIBE] Waiting for session ← {topic}")
                    session_info, _ = await slim.receive()
                    session, msg_bytes = await slim.receive(session=session_info.id)
                    
                    # Process message
                    msg_text = msg_bytes.decode()
                    logger.info(f"[SUBSCRIBE] Got message ← {topic} ({len(msg_text)} chars)")
                    message = Message.deserialize(msg_text) if msg_text.startswith('{"') else Message(payload=msg_text)
                    
                    logger.info(f"[SUBSCRIBE] Processing message ← {topic}")
                    response = await handler(message)
                    
                    # Send response if available
                    if response and hasattr(response, 'serialize'):
                        logger.info(f"[SUBSCRIBE] Sending response → {topic}")
                        await slim.publish_to(session, response.serialize())
                        logger.info(f"[SUBSCRIBE] Response sent ✓ {topic}")
                    else:
                        logger.info(f"[SUBSCRIBE] No response to send ← {topic}")
        
        asyncio.create_task(simple_receive())

    async def receive_back(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Receive a message from a topic."""
        logger.info(f"receive_back() - Called with topic: {topic}, respond: {respond}")
        logger.warning(f"receive_back() - This is a no-op method for compatibility")
        pass


    def _sanitize_topic(self, topic: str) -> str:
        """Sanitize topic name for SLIM compatibility."""
        logger.info(f"_sanitize_topic() - Sanitizing topic: '{topic}'")
        # Replace spaces and special characters
        sanitized = topic.replace(" ", "_").replace("-", "_")
        # Remove any other problematic characters
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        logger.info(f"_sanitize_topic() - Result: '{topic}' -> '{sanitized}'")
        return sanitized
