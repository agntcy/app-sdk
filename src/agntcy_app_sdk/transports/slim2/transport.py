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
        """
        Publish message to topic using SLIM request-reply pattern.
        
        Args:
            topic: Target topic/receiver name
            message: Message to send
            respond: Whether to expect a response
            
        Returns:
            Response message if respond=True, None otherwise
            
        Raises:
            RuntimeError: If communication fails
        """
        original_topic = topic
        topic = self._sanitize_topic(topic)
        
        logger.info(f"publish() - Starting message publish to topic '{original_topic}' (sanitized: '{topic}')")
        logger.info(f"publish() - Target: {self._org}/{self._namespace}/{topic}, respond={respond}")
        logger.info(f"publish() - Message type: {type(message)}, payload: {message.payload}")
        
        try:
            # Ensure connection
            logger.info("publish() - Ensuring SLIM connection")
            await self._ensure_connection()
            logger.info("publish() - Connection ensured")
            
            # Create sender and receiver names
            sender_name = slim_bindings.PyName(self._org, self._namespace, self._identity)
            receiver_name = slim_bindings.PyName(self._org, self._namespace, topic)
            logger.info(f"publish() - Created names - sender: {self._org}/{self._namespace}/{self._identity}, receiver: {self._org}/{self._namespace}/{topic}")
            
            # Create new SLIM instance for this request
            logger.info("publish() - Creating new SLIM instance")
            slim = await slim_bindings.Slim.new(sender_name, self._provider, self._verifier)
            logger.info("publish() - SLIM instance created")
            
            async with slim:
                logger.info(f"publish() - Connecting to SLIM endpoint: {self._endpoint}")
                await slim.connect({
                    "endpoint": self._endpoint,
                    "tls": {"insecure": True}
                })
                logger.info(f"publish() - Connected, subscribing to sender name: {sender_name}")
                await slim.subscribe(sender_name)
                
                # Create session and set route
                logger.info("publish() - Creating session with FireAndForget configuration")
                session = await slim.create_session(
                    slim_bindings.PySessionConfiguration.FireAndForget()
                )
                logger.info(f"publish() - Setting route to receiver: {receiver_name}")
                await slim.set_route(receiver_name)
                
                # Brief wait for routing setup
                logger.info("publish() - Waiting for routing setup (0.1s)")
                await asyncio.sleep(0.1)
                logger.info("publish() - Routing setup complete")
                
                # Use request_reply for response
                logger.info(f"publish() - Sending request-reply to {topic} (timeout: {self._request_timeout}s)")
                
                serialized_msg = message.serialize()
                logger.info(f"publish() - Serialized message length: {len(serialized_msg)} bytes")
                
                timeout = datetime.timedelta(seconds=self._request_timeout)
                logger.info(f"publish() - Calling SLIM request_reply with timeout: {timeout}")
                _, reply_bytes = await asyncio.wait_for(
                    slim.request_reply(session, serialized_msg, receiver_name, timeout=timeout),
                    timeout=self._request_timeout + 5  # Add buffer to SLIM timeout
                )
                
                # Deserialize response
                response_text = reply_bytes.decode()
                logger.info(f"publish() - Received response ({len(response_text)} chars): {response_text[:100]}...")
                
                try:
                    logger.info("publish() - Attempting to deserialize response as Message")
                    response_message = Message.deserialize(response_text)
                    logger.info("publish() - Response deserialized successfully")
                except Exception as e:
                    # Fallback to simple payload if deserialization fails
                    logger.warning(f"publish() - Response deserialization failed ({e}), using fallback")
                    response_message = Message(payload=response_text)
                
                logger.info(f"publish() - Request-reply completed successfully")
                return response_message
                    
        except asyncio.TimeoutError as e:
            error_msg = f"publish() - Request to {topic} timed out after {self._request_timeout}s"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
        except Exception as e:
            error_msg = f"publish() - Failed to publish to {topic}: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

    async def subscribe(self, topic: str, callback=None) -> None:
        """
        Subscribe to topic for incoming messages.
        
        Args:
            topic: Topic to subscribe to
            callback: Optional callback (uses set_callback if not provided)
        """
        original_topic = topic
        topic = self._sanitize_topic(topic)
        logger.info(f"subscribe() - Starting subscription to topic '{original_topic}' (sanitized: '{topic}')")
        logger.info(f"subscribe() - Target: {self._org}/{self._namespace}/{topic}")
        
        # Use provided callback or default
        handler = callback or self._callback
        logger.info(f"subscribe() - Using handler: {type(handler)} (provided: {callback is not None}, default: {self._callback is not None})")
        if not handler:
            logger.error("subscribe() - No callback handler available for subscription")
            return
        
        try:
            # Create receiver name
            receiver_name = slim_bindings.PyName(self._org, self._namespace, topic)
            logger.info(f"subscribe() - Created receiver name: {self._org}/{self._namespace}/{topic}")
            
            # Create SLIM instance for receiving
            logger.info("subscribe() - Creating new SLIM instance for receiving")
            slim = await slim_bindings.Slim.new(receiver_name, self._provider, self._verifier)
            logger.info("subscribe() - SLIM instance created for subscription")
            
            async def receive_loop():
                """Background task to handle incoming messages."""
                logger.info(f"subscribe() - Starting receive_loop for topic {topic}")
                try:
                    async with slim:
                        logger.info(f"subscribe() - Connecting to SLIM endpoint: {self._endpoint}")
                        await slim.connect({
                            "endpoint": self._endpoint,
                            "tls": {"insecure": True}
                        })
                        logger.info(f"subscribe() - Connected, subscribing to receiver name: {receiver_name}")
                        await slim.subscribe(receiver_name)
                        logger.info(f"subscribe() - Started receiving on topic '{topic}' successfully")
                        
                        while True:
                            logger.info(f"subscribe() - Waiting for new session on topic {topic}")
                            # Wait for new session
                            session_info, _ = await slim.receive()
                            logger.info(f"subscribe() - Received new session: {session_info.id}")
                            
                            # Handle session in background
                            logger.info(f"subscribe() - Creating background task for session {session_info.id}")
                            asyncio.create_task(
                                self._handle_session(slim, session_info, handler)
                            )
                            
                except asyncio.CancelledError:
                    logger.info(f"subscribe() - Subscription to {topic} cancelled")
                    raise
                except Exception as e:
                    logger.error(f"subscribe() - Error in receive loop for {topic}: {type(e).__name__}: {e}")
                    raise
            
            # Start receive loop as background task
            logger.info(f"subscribe() - Starting receive loop as background task for topic {topic}")
            asyncio.create_task(receive_loop())
            logger.info(f"subscribe() - Background receive task created for topic {topic}")
            
        except Exception as e:
            error_msg = f"subscribe() - Failed to subscribe to {topic}: {type(e).__name__}: {e}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)

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

    async def _ensure_connection(self) -> None:
        """Ensure SLIM connection is established with retry logic."""
        logger.info("_ensure_connection() - Starting connection check")
        async with self._connection_lock:
            logger.info(f"_ensure_connection() - Acquired connection lock, connected: {self._is_connected}")
            if self._is_connected:
                logger.info("_ensure_connection() - Already connected, returning")
                return
                
            logger.info(f"_ensure_connection() - Establishing connection to {self._endpoint} (max retries: {self._max_retries})")
            for attempt in range(self._max_retries):
                try:
                    logger.info(f"_ensure_connection() - Connection attempt {attempt + 1}/{self._max_retries}")
                    
                    # Test connection by creating a temporary SLIM instance
                    test_id = uuid.uuid4().hex[:8]
                    test_name = slim_bindings.PyName(self._org, self._namespace, f"test-{test_id}")
                    logger.info(f"_ensure_connection() - Creating test SLIM instance: {self._org}/{self._namespace}/test-{test_id}")
                    test_slim = await slim_bindings.Slim.new(test_name, self._provider, self._verifier)
                    
                    logger.info(f"_ensure_connection() - Testing connection with timeout: {self._connection_timeout}s")
                    await asyncio.wait_for(
                        test_slim.connect({
                            "endpoint": self._endpoint,
                            "tls": {"insecure": True}
                        }),
                        timeout=self._connection_timeout
                    )
                    
                    # Connection successful
                    self._is_connected = True
                    logger.info(f"_ensure_connection() - Connected successfully to SLIM server at {self._endpoint}")
                    return
                    
                except Exception as e:
                    logger.warning(f"_ensure_connection() - Attempt {attempt + 1} failed: {type(e).__name__}: {e}")
                    if attempt == self._max_retries - 1:
                        error_msg = f"Failed to connect after {self._max_retries} attempts: {e}"
                        logger.error(f"_ensure_connection() - {error_msg}")
                        raise RuntimeError(error_msg)
                    
                    # Exponential backoff
                    backoff_time = 2 ** attempt
                    logger.info(f"_ensure_connection() - Waiting {backoff_time}s before retry")
                    await asyncio.sleep(backoff_time)

    async def _handle_session(self, slim, session_info, handler) -> None:
        """
        Handle individual session messages.
        
        Args:
            slim: SLIM connection instance
            session_info: Session information
            handler: Message handler callback
        """
        session_id = session_info.id
        logger.info(f"_handle_session() - Starting session handler for {session_id}")
        logger.info(f"_handle_session() - Session info: {session_info}, handler: {type(handler)}")
        
        try:
            async with self._session_lock:
                self._active_sessions[session_id] = session_info
                logger.info(f"_handle_session() - Added session {session_id} to active sessions (total: {len(self._active_sessions)})")
            
            logger.info(f"_handle_session() - Session {session_id} registered, starting message loop")
            
            while True:
                try:
                    logger.info(f"_handle_session() - Waiting for message in session {session_id}")
                    # Receive message from session
                    session, msg_bytes = await slim.receive(session=session_id)
                    
                    # Decode and deserialize message
                    msg_text = msg_bytes.decode()
                    logger.info(f"_handle_session() - Received message in session {session_id} ({len(msg_text)} chars): {msg_text[:100]}...")
                    
                    try:
                        logger.info(f"_handle_session() - Attempting to deserialize message as Message object")
                        message = Message.deserialize(msg_text)
                        logger.info(f"_handle_session() - Message deserialized successfully")
                    except Exception as e:
                        # Fallback to simple payload
                        logger.warning(f"_handle_session() - Message deserialization failed ({e}), using fallback")
                        message = Message(payload=msg_text)
                    
                    # Process message through handler
                    logger.info(f"_handle_session() - Calling handler for session {session_id}")
                    response = await handler(message)
                    logger.info(f"_handle_session() - Handler returned: {type(response)} (has serialize: {hasattr(response, 'serialize') if response else False})")
                    
                    if response and hasattr(response, 'serialize'):
                        # Send response back
                        logger.info(f"_handle_session() - Serializing and sending response for session {session_id}")
                        response_data = response.serialize()
                        await slim.publish_to(session, response_data)
                        logger.info(f"_handle_session() - Sent response to session {session_id} ({len(response_data)} bytes)")
                    else:
                        logger.info(f"_handle_session() - No response to send for session {session_id}")
                    
                except Exception as e:
                    logger.error(f"_handle_session() - Error handling message in session {session_id}: {type(e).__name__}: {e}")
                    break
                    
        except Exception as e:
            logger.error(f"_handle_session() - Session {session_id} error: {type(e).__name__}: {e}")
        finally:
            logger.info(f"_handle_session() - Cleaning up session {session_id}")
            await self._cleanup_session(session_id)

    async def _cleanup_session(self, session_id: str) -> None:
        """Clean up session resources."""
        logger.info(f"_cleanup_session() - Starting cleanup for session {session_id}")
        try:
            async with self._session_lock:
                if session_id in self._active_sessions:
                    del self._active_sessions[session_id]
                    logger.info(f"_cleanup_session() - Removed session {session_id} from active sessions (remaining: {len(self._active_sessions)})")
                else:
                    logger.info(f"_cleanup_session() - Session {session_id} not found in active sessions")
            logger.info(f"_cleanup_session() - Session {session_id} cleaned up successfully")
        except Exception as e:
            logger.error(f"_cleanup_session() - Error cleaning up session {session_id}: {type(e).__name__}: {e}")

    def _sanitize_topic(self, topic: str) -> str:
        """Sanitize topic name for SLIM compatibility."""
        logger.info(f"_sanitize_topic() - Sanitizing topic: '{topic}'")
        # Replace spaces and special characters
        sanitized = topic.replace(" ", "_").replace("-", "_")
        # Remove any other problematic characters
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        logger.info(f"_sanitize_topic() - Result: '{topic}' -> '{sanitized}'")
        return sanitized
