# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import asyncio
import datetime
import logging
import uuid
import os
from typing import Optional, List, Dict, Any, Callable
import slim_bindings

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.transports.transport import BaseTransport
from agntcy_app_sdk.protocols.message import Message

configure_logging()
logger = get_logger(__name__)

class SLIM3Transport(BaseTransport):
    """
    Generic SLIM v0.4.0 Transport Implementation
    
    Combines the clean slimbrew pattern with robust transport functionality.
    Features:
    - Generic configuration (no hardcoded names)
    - Environment-based setup
    - Broadcast and point-to-point messaging
    - Session management and streaming
    - Pub/sub and request-reply patterns
    """

    def __init__(
        self,
        endpoint: str = None,
        org: str = None,
        namespace: str = None,
        identity: str = None,
        shared_secret: str = None,
        connection_timeout: int = 10,
        request_timeout: int = 30,
        max_retries: int = 3,
        is_moderator: bool = None,
    ):
        """
        Initialize SLIM3 Transport with environment-first configuration.
        
        Args:
            endpoint: SLIM server endpoint (fallback to SLIM_ENDPOINT env)
            org: Organization namespace (fallback to SLIM_ORG env or 'default')
            namespace: Service namespace (fallback to SLIM_NAMESPACE env or 'default') 
            identity: Agent identity (fallback to SLIM_IDENTITY env or generated)
            shared_secret: Authentication secret (fallback to SLIM_SHARED_SECRET env)
            connection_timeout: Connection timeout in seconds
            request_timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
        """
        # Environment-first configuration
        self._endpoint = endpoint or os.environ.get("SLIM_ENDPOINT", "http://localhost:46357")
        self._org = org or os.environ.get("SLIM_ORG", "default")
        self._namespace = namespace or os.environ.get("SLIM_NAMESPACE", "default")
        self._identity = identity or os.environ.get("SLIM_IDENTITY", f"agent-{uuid.uuid4().hex[:8]}")
        self._shared_secret = shared_secret or os.environ.get("SLIM_SHARED_SECRET", "slim-transport-dev-secret-2024")
        
        # Role detection from environment
        moderator_name = os.environ.get("MODERATOR_NAME", "moderator")
        if is_moderator is None:
            self._is_moderator = moderator_name.lower() in self._identity.lower()
        else:
            self._is_moderator = is_moderator
        
        self._connection_timeout = connection_timeout
        self._request_timeout = request_timeout
        self._max_retries = max_retries
        
        # Connection and session management
        self._active_connections: Dict[str, Any] = {}
        self._active_sessions: Dict[str, Any] = {}  # channel_name -> (session_info, slim_app)
        self._connection_lock = asyncio.Lock()
        self._session_lock = asyncio.Lock()
        
        # Channel configuration from environment
        self._group_channel = os.environ.get("GROUP_CHANNEL", "group")
        
        # Participant channels from environment (comma-separated)
        participants_env = os.environ.get("PARTICIPANT_CHANNELS", "vietnam,colombia,brazil")
        participant_list = [p.strip() for p in participants_env.split(",")]
        self._participant_channels = {p: p for p in participant_list}
        
        # Persistent SLIM app for session reuse
        self._slim_app = None
        
        # Message callback
        self._callback = None
        
        # Authentication setup
        self._provider = None
        self._verifier = None
        if self._shared_secret:
            self._provider = slim_bindings.PyIdentityProvider.SharedSecret(
                self._identity, self._shared_secret
            )
            self._verifier = slim_bindings.PyIdentityVerifier.SharedSecret(
                self._identity, self._shared_secret
            )
            pass
        else:
            logger.warning("No shared_secret provided")

    @classmethod
    def from_client(cls, client: Any) -> "SLIM3Transport":
        """Create transport from existing SLIM client."""
        logger.warning("from_client() not supported - SLIM3Transport manages its own connections")
        raise NotImplementedError("SLIM3Transport manages its own connections")

    @classmethod
    def from_config(cls, endpoint: str, **kwargs) -> "SLIM3Transport":
        """Create transport from configuration."""
        logger.info(f"Creating SLIM3Transport from config: {endpoint}")
        return cls(endpoint=endpoint, **kwargs)

    def type(self) -> str:
        """Return transport type."""
        return "SLIM3"

    async def close(self) -> None:
        """Close all active connections and sessions."""
        # Close persistent SLIM app
        if self._slim_app:
            try:
                await self._slim_app.close()
            except Exception as e:
                logger.warning(f"Error closing SLIM app: {e}")
            self._slim_app = None
        
        async with self._connection_lock:
            self._active_connections.clear()
        
        async with self._session_lock:
            self._active_sessions.clear()

    def set_callback(self, handler: Callable[[Message], asyncio.Future]) -> None:
        """Set message handler callback."""
        self._callback = handler

    def add_participant(self, name: str, channel: str = None) -> None:
        """Simple way to add a participant - easy to use."""
        if self._is_moderator:
            channel = channel or f"{name}_channel"
            self._participant_channels[name] = channel
    
    async def publish(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Publish a message to a topic."""
        topic = self._sanitize_topic(topic)
        
        # Check if topic specifies a participant (e.g., "vietnam", "colombia", "brazil")
        to_participant = topic if topic in self._participant_channels else None
        
        # Simple logic: moderator vs participant
        if self._is_moderator:
            return await self._send_as_moderator(topic, message, to_participant, respond)
        else:
            return await self._send_as_participant(message, respond)
    
    async def _send_as_moderator(self, topic: str, message: Message, to_participant: str = None, respond: bool = False) -> Optional[Message]:
        """Moderator sends to group or specific participant - simple logic."""
        
        # Pick target channel - simple if/else
        if to_participant:
            channel = self._participant_channels.get(to_participant, f"{to_participant}_channel")
        else:
            channel = self._group_channel
        
        # Get session and send
        session_info, slim_app = await self._get_or_create_session(channel)
        channel_name = slim_bindings.PyName(self._org, self._namespace, channel)
        await slim_app.publish(session_info, message.serialize(), channel_name)
    
        if respond:
            # Wait for response
            _, response_bytes = await slim_app.receive(session=session_info.id)
            response_text = response_bytes.decode()
            try:
                return Message.deserialize(response_text)
            except Exception:
                return Message(payload=response_text)
        return None
    
    async def _send_as_participant(self, message: Message, respond: bool = False) -> Optional[Message]:
        """Participant always sends to group - super simple."""
        
        # Participants always use group channel - no choice!
        channel = self._group_channel
        
        # Get SLIM app and wait for invite
        slim_app = await self._get_persistent_slim_app()
        session_info, _ = await slim_app.receive()
        
        # Send to group channel
        channel_name = slim_bindings.PyName(self._org, self._namespace, channel)
        await slim_app.publish(session_info, message.serialize(), channel_name)

        if respond:
            # Wait for response
            _, response_bytes = await slim_app.receive(session=session_info.id)
            response_text = response_bytes.decode()
            try:
                return Message.deserialize(response_text)
            except Exception:
                return Message(payload=response_text)
        return None

    async def broadcast(
        self,
        topic: str,
        message: Message,
        expected_responses: int = 1,
        timeout: Optional[float] = 30.0,
    ) -> None:
        """Broadcast a message to all subscribers of a topic and wait for responses."""
        # Use all known participants for broadcast
        invitees = list(self._participant_channels.keys())
        
        # Use group channel for broadcast
        channel_name = slim_bindings.PyName(self._org, self._namespace, self._group_channel)
        
        # Get session and broadcast
        session_info, slim_app = await self._get_or_create_session(self._group_channel)
        
        # Invite all participants - simple loop
        for participant in invitees:
            participant_name = slim_bindings.PyName(self._org, self._namespace, participant)
            await slim_app.set_route(participant_name)
            await slim_app.invite(session_info, participant_name)
        
        await asyncio.sleep(1)  # Wait for joins
        
        # Send message
        await slim_app.publish(session_info, message.serialize(), channel_name)

        # Collect responses
        responses = []
        for _ in range(expected_responses):
            try:
                _, response_bytes = await asyncio.wait_for(
                    slim_app.receive(session=session_info.id), 
                    timeout=timeout
                )
                response_text = response_bytes.decode()
                try:
                    response_msg = Message.deserialize(response_text)
                except Exception:
                    response_msg = Message(payload=response_text)
                responses.append(response_msg)
            except asyncio.TimeoutError:
                break
        
        return responses

    async def subscribe(self, topic: str, callback: callable = None) -> None:
        """Subscribe to a topic with a callback."""
        handler = callback or self._callback
        if not handler:
            logger.error(f"No callback handler for topic {topic}")
            return
            
        topic = self._sanitize_topic(topic)
        logger.info(f"[SUBSCRIBE] ← {self._org}/{self._namespace}/{topic}")
        
        # Create receiver identity and SLIM app
        receiver_name = slim_bindings.PyName(self._org, self._namespace, topic)
        if not self._provider or not self._verifier:
            raise ValueError("Authentication not configured - shared_secret required")
        slim_app = await slim_bindings.Slim.new(receiver_name, self._provider, self._verifier)
        
        async def subscription_handler():
            """Background task for handling subscriptions."""
            try:
                async with slim_app:
                    await slim_app.connect({
                        "endpoint": self._endpoint,
                        "tls": {"insecure": True}
                    })
                    await slim_app.subscribe(receiver_name)
                    logger.info(f"[SUBSCRIBE] Ready ← {topic}")
                    
                    while True:
                        logger.info(f"[SUBSCRIBE] Waiting for session ← {topic}")
                        session_info, _ = await slim_app.receive()
                        session, msg_bytes = await slim_app.receive(session=session_info.id)
                        
                        # Process message
                        msg_text = msg_bytes.decode()
                        logger.info(f"[SUBSCRIBE] Message ← {topic} ({len(msg_text)} chars)")
                        
                        try:
                            message = Message.deserialize(msg_text)
                        except Exception:
                            message = Message(payload=msg_text)
                        
                        # Handle message
                        logger.info(f"[SUBSCRIBE] Processing ← {topic}")
                        if asyncio.iscoroutinefunction(handler):
                            response = await handler(message)
                        else:
                            response = handler(message)
                        
                        # Send response if available
                        if response and hasattr(response, 'serialize'):
                            logger.info(f"[SUBSCRIBE] Sending response → {topic}")
                            await slim_app.publish_to(session, response.serialize())
                            logger.info(f"[SUBSCRIBE] Response sent ✓ {topic}")
                        
            except Exception as e:
                logger.error(f"[SUBSCRIBE] Error ← {topic}: {e}")
                raise
        
        # Start subscription task
        asyncio.create_task(subscription_handler())
    
    async def receive_back(
        self,
        topic: str,
        message: Message,
        respond: Optional[bool] = False,
    ) -> None:
        """Receive a message from a topic."""
        # For compatibility - no-op in SLIM3Transport
        pass

    async def _get_or_create_session(self, topic: str) -> tuple[Any, Any]:
        """
        Get or create a reusable session for the given topic (moderator only).
        
        Args:
            topic: Channel topic name
            
        Returns:
            Tuple of (session_info, slim_app)
        """
        if not self._is_moderator:
            raise ValueError("Only moderators can create sessions")
        
        async with self._session_lock:
            if topic in self._active_sessions:
                logger.info(f"[MODERATOR] Reusing existing session for {topic}")
                return self._active_sessions[topic]
            
            # Create new session
            logger.info(f"[MODERATOR] Creating new session for {topic}")
            channel_name = slim_bindings.PyName(self._org, self._namespace, topic)
            
            # Get persistent SLIM app
            slim_app = await self._get_persistent_slim_app()
            
            # Create streaming session
            session_info = await slim_app.create_session(
                slim_bindings.PySessionConfiguration.Streaming(
                    slim_bindings.PySessionDirection.BIDIRECTIONAL,
                    topic=channel_name,
                    moderator=True,
                    max_retries=self._max_retries,
                    timeout=datetime.timedelta(seconds=self._request_timeout),
                    mls_enabled=True,
                )
            )
            
            # Store session for reuse
            self._active_sessions[topic] = (session_info, slim_app)
            logger.info(f"[MODERATOR] Session created and stored: {session_info.id}")
            
            return session_info, slim_app
    
    async def _get_persistent_slim_app(self) -> slim_bindings.PyService:
        """
        Get or create persistent SLIM app instance.
        
        Returns:
            Connected SLIM app instance
        """
        if self._slim_app is None:
            agent_name = slim_bindings.PyName(self._org, self._namespace, self._identity)
            if not self._provider or not self._verifier:
                raise ValueError("Authentication not configured - shared_secret required")
            self._slim_app = await slim_bindings.Slim.new(agent_name, self._provider, self._verifier)
            
            # Connect only
            await self._slim_app.connect({
                "endpoint": self._endpoint,
                "tls": {"insecure": True}
            })
        
        return self._slim_app
    

    def _sanitize_topic(self, topic: str) -> str:
        """
        Sanitize topic name for SLIM compatibility.
        
        Args:
            topic: Raw topic name
            
        Returns:
            Sanitized topic name
        """
        # Replace spaces and special characters
        sanitized = topic.replace(" ", "_").replace("-", "_")
        # Remove any other problematic characters
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "_")
        return sanitized
