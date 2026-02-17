# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

"""Experimental A2A client for communication patterns beyond the A2A spec.

The standard A2A client handles point-to-point request/response between
two agents. This module extends that model with experimental operations
— broadcast (publish/subscribe) and multi-party group chat — over
non-HTTP transports such as SLIM and NATS.

The client preserves core A2A benefits: AgentCard-based discovery,
JSON-RPC message envelopes, and typed ``MessageSendParams`` payloads.
Standard A2A operations (send_message, get_task, etc.) delegate to the
inner upstream ``Client``; experimental operations (broadcast_message,
start_groupchat) delegate directly to the underlying ``BaseTransport``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, List
from uuid import uuid4

from a2a.client.client import Client, ClientEvent
from a2a.client.middleware import ClientCallContext
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    Message as A2AMessage,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
)

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.client.utils import (
    get_identity_auth_error,
    message_translator,
)
from agntcy_app_sdk.transport.base import BaseTransport

configure_logging()
logger = get_logger(__name__)


class A2AExperimentalClient(Client):
    """Subclass of the upstream ``a2a.client.Client`` ABC that adds
    experimental transport methods (broadcast, groupchat).

    Standard A2A operations delegate to the inner upstream ``Client``.
    Experimental operations delegate directly to a ``BaseTransport``.

    This class is only returned by the factory when negotiation selects
    a patterns transport (``slimpatterns`` / ``natspatterns``).  The
    ``transport`` and ``topic`` fields are therefore always present.
    """

    def __init__(
        self,
        client: Client,
        agent_card: AgentCard,
        transport: BaseTransport,
        topic: str,
    ) -> None:
        # Initialise the ABC with the inner client's consumers/middleware
        super().__init__(
            consumers=list(client._consumers),
            middleware=list(client._middleware),
        )
        self._client = client
        self._agent_card = agent_card
        self._transport = transport
        self._topic = topic

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
    def transport(self) -> BaseTransport:
        """The underlying ``BaseTransport``."""
        return self._transport

    @property
    def topic(self) -> str:
        """The topic used for transport operations."""
        return self._topic

    # ------------------------------------------------------------------
    # Client ABC — delegate to upstream Client
    # ------------------------------------------------------------------

    async def send_message(
        self,
        request: A2AMessage,
        *,
        context: ClientCallContext | None = None,
        request_metadata: dict[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[ClientEvent | A2AMessage]:
        """Send a message via the upstream client."""
        async for event in self._client.send_message(
            request,
            context=context,
            request_metadata=request_metadata,
            extensions=extensions,
        ):
            yield event

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieve a task from the upstream client."""
        return await self._client.get_task(
            request, context=context, extensions=extensions
        )

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Cancel a task via the upstream client."""
        return await self._client.cancel_task(
            request, context=context, extensions=extensions
        )

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Set push notification config via the upstream client."""
        return await self._client.set_task_callback(
            request, context=context, extensions=extensions
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Get push notification config via the upstream client."""
        return await self._client.get_task_callback(
            request, context=context, extensions=extensions
        )

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribe to task updates via the upstream client."""
        async for event in self._client.resubscribe(
            request, context=context, extensions=extensions
        ):
            yield event

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AgentCard:
        """Return the locally-cached agent card."""
        return self._agent_card

    # ------------------------------------------------------------------
    # Experimental operations — broadcast & groupchat via BaseTransport
    # ------------------------------------------------------------------

    async def broadcast_message(
        self,
        request: SendMessageRequest | SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        timeout: float = 60.0,
    ) -> List[SendMessageResponse]:
        """Broadcast a message to multiple recipients via transport."""
        if not request.id:
            request.id = str(uuid4())

        msg = message_translator(
            request=request.model_dump(mode="json", exclude_none=True)
        )

        if not broadcast_topic:
            broadcast_topic = self._topic

        try:
            responses = await self._transport.gather(
                broadcast_topic,
                msg,
                recipients=recipients,
                timeout=timeout,
            )
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error gathering A2A request with transport {self._transport.type()}: {e}"
            )
            return []

        broadcast_responses = []
        for raw_resp in responses:
            try:
                resp = json.loads(raw_resp.payload.decode("utf-8"))
                broadcast_responses.append(SendMessageResponse(resp))
            except Exception as e:
                logger.error(f"Error decoding JSON response: {e}")
                continue

        return broadcast_responses

    async def broadcast_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str | None = None,
        message_limit: int | None = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[SendMessageResponse]:
        """Broadcast with streaming responses."""
        if not request.id:
            request.id = str(uuid4())

        msg = message_translator(
            request=request.model_dump(mode="json", exclude_none=True)
        )

        if not broadcast_topic:
            broadcast_topic = self._topic

        # determine how many messages to stream until we break out
        # if none, set strict number of recipients messages
        if message_limit is None:
            message_limit = len(recipients)

        try:
            async for raw_resp in self._transport.gather_stream(
                broadcast_topic,
                msg,
                recipients=recipients,
                message_limit=message_limit,
                timeout=timeout,
            ):
                try:
                    logger.info(raw_resp)
                    resp = json.loads(raw_resp.payload.decode("utf-8"))
                    if resp.get("error") == "forbidden" or raw_resp.status_code == 403:
                        logger.warning(
                            f"Received forbidden error in broadcast streaming response: {resp}"
                        )
                        yield SendMessageResponse(get_identity_auth_error())
                    else:
                        yield SendMessageResponse(resp)
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error gathering streaming A2A request with transport {self._transport.type()}: {e}"
            )
            return

    async def start_groupchat(
        self,
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> List[SendMessageResponse]:
        """Start a group chat conversation via transport."""
        if not init_message.id:
            init_message.id = str(uuid4())

        msg = message_translator(
            request=init_message.model_dump(mode="json", exclude_none=True)
        )
        try:
            member_messages = await self._transport.start_conversation(
                group_channel=group_channel,
                participants=participants,
                init_message=msg,
                end_message=end_message,
                timeout=timeout,
            )
            groupchat_messages = []
            for raw_msg in member_messages:
                try:
                    resp = json.loads(raw_msg.payload.decode("utf-8"))
                    groupchat_messages.append(SendMessageResponse(resp))
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue

            return groupchat_messages
        except (TimeoutError, asyncio.CancelledError):
            raise
        except Exception as e:
            logger.error(
                f"Error starting group chat A2A request with transport {self._transport.type()}: {e}"
            )
            return []

    async def start_streaming_groupchat(
        self,
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> AsyncIterator[SendMessageResponse]:
        """Start a streaming group chat conversation via transport."""
        if not init_message.id:
            init_message.id = str(uuid4())

        msg = message_translator(
            request=init_message.model_dump(mode="json", exclude_none=True)
        )

        async for raw_member_message in self._transport.start_streaming_conversation(
            group_channel=group_channel,
            participants=participants,
            init_message=msg,
            end_message=end_message,
            timeout=timeout,
        ):
            message = json.loads(raw_member_message.payload.decode("utf-8"))
            yield SendMessageResponse(message)
