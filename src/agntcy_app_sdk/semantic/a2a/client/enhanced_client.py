# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from typing import Any, AsyncIterator, List

from a2a.client.client import Client, ClientEvent
from a2a.types import (
    AgentCard,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    Message as A2AMessage,
    Task,
)

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.client.experimental import (
    experimental_a2a_transport_methods,
)
from agntcy_app_sdk.transport.base import BaseTransport

configure_logging()
logger = get_logger(__name__)


class A2AEnhancedClient:
    """Wrapper around the upstream ``a2a.client.Client`` that adds
    experimental transport methods (broadcast, groupchat) via composition
    rather than monkey-patching.

    Standard A2A operations delegate to the upstream ``Client``.
    Experimental operations delegate directly to a ``BaseTransport``.
    """

    def __init__(
        self,
        client: Client,
        agent_card: AgentCard,
        transport: BaseTransport | None = None,
        topic: str | None = None,
    ) -> None:
        self._client = client
        self._agent_card = agent_card
        self._transport = transport
        self._topic = topic

        # Wire up experimental methods if we have a transport
        self._experimental: dict[str, Any] = {}
        if transport and topic:
            self._experimental = experimental_a2a_transport_methods(transport, topic)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def agent_card(self) -> AgentCard:
        """The agent card for this client."""
        return self._agent_card

    @property
    def upstream_client(self) -> Client:
        """Access to the underlying upstream ``Client``."""
        return self._client

    @property
    def transport(self) -> BaseTransport | None:
        """The underlying ``BaseTransport``, if any."""
        return self._transport

    @property
    def topic(self) -> str | None:
        """The topic used for transport operations."""
        return self._topic

    # ------------------------------------------------------------------
    # Standard A2A operations — delegate to upstream Client
    # ------------------------------------------------------------------

    async def send_message(
        self,
        request: SendMessageRequest | A2AMessage,
        **kwargs: Any,
    ) -> AsyncIterator[ClientEvent | A2AMessage]:
        """Send a message via the upstream client.

        Yields the same ``ClientEvent | Message`` events as the upstream
        ``Client.send_message()``.  A ``ClientEvent`` is a
        ``(Task, UpdateEvent | None)`` tuple.
        """
        msg = request.params.message if hasattr(request, "params") else request
        async for event in self._client.send_message(msg, **kwargs):
            yield event

    async def get_task(self, request: Any, **kwargs: Any) -> Task:
        """Retrieve a task from the upstream client."""
        return await self._client.get_task(request, **kwargs)

    async def cancel_task(self, request: Any, **kwargs: Any) -> Task:
        """Cancel a task via the upstream client."""
        return await self._client.cancel_task(request, **kwargs)

    # ------------------------------------------------------------------
    # Experimental operations — delegate to BaseTransport
    # ------------------------------------------------------------------

    async def broadcast_message(
        self,
        request: SendMessageRequest | SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        timeout: float = 60.0,
    ) -> List[SendMessageResponse]:
        """Broadcast a message to multiple recipients via transport."""
        fn = self._experimental.get("broadcast_message")
        if fn is None:
            raise RuntimeError(
                "broadcast_message requires a transport with experimental patterns"
            )
        return await fn(
            request,
            recipients=recipients,
            broadcast_topic=broadcast_topic,
            timeout=timeout,
        )

    async def broadcast_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        message_limit: int | None = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[SendMessageResponse]:
        """Broadcast with streaming responses."""
        fn = self._experimental.get("broadcast_message_streaming")
        if fn is None:
            raise RuntimeError(
                "broadcast_message_streaming requires a transport "
                "with experimental patterns"
            )
        async for resp in fn(
            request,
            recipients=recipients,
            broadcast_topic=broadcast_topic,
            message_limit=message_limit,
            timeout=timeout,
        ):
            yield resp

    async def start_groupchat(
        self,
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> List[SendMessageResponse]:
        """Start a group chat conversation via transport."""
        fn = self._experimental.get("start_groupchat")
        if fn is None:
            raise RuntimeError(
                "start_groupchat requires a transport with experimental patterns"
            )
        return await fn(
            init_message,
            group_channel=group_channel,
            participants=participants,
            timeout=timeout,
            end_message=end_message,
        )

    async def start_streaming_groupchat(
        self,
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> AsyncIterator[SendMessageResponse]:
        """Start a streaming group chat conversation via transport."""
        fn = self._experimental.get("start_streaming_groupchat")
        if fn is None:
            raise RuntimeError(
                "start_streaming_groupchat requires a transport "
                "with experimental patterns"
            )
        async for resp in fn(
            init_message,
            group_channel=group_channel,
            participants=participants,
            timeout=timeout,
            end_message=end_message,
        ):
            yield resp
