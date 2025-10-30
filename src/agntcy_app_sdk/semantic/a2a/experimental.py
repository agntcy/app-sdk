# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, List, AsyncIterator, Dict, Callable
from uuid import uuid4

from a2a.types import (
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendMessageResponse,
)

from agntcy_app_sdk.semantic.a2a.utils import message_translator
from agntcy_app_sdk.transport.base import BaseTransport
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


def experimental_a2a_transport_methods(
    transport: BaseTransport, topic: str
) -> Dict[str, Callable[..., Any]]:
    """
    Experimental A2A transport methods for group chats and broadcasting.
    """

    async def start_groupchat(
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        muted_participants: List[str] = [],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> List[SendMessageResponse]:
        if not init_message.id:
            init_message.id = str(uuid4())

        msg = message_translator(
            request=init_message.model_dump(mode="json", exclude_none=True)
        )
        try:
            member_messages = await transport.start_conversation(
                group_channel=group_channel,
                participants=participants,
                muted_participants=muted_participants,
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
        except Exception as e:
            logger.error(
                f"Error starting group chat A2A request with transport {transport.type()}: {e}"
            )
            return []

    async def start_streaming_groupchat(
        init_message: SendMessageRequest,
        group_channel: str,
        participants: List[str],
        muted_participants: List[str] = [],
        timeout: float = 60,
        end_message: str = "work-done",
    ) -> AsyncIterator[SendMessageResponse]:
        if not init_message.id:
            init_message.id = str(uuid4())

        msg = message_translator(
            request=init_message.model_dump(mode="json", exclude_none=True)
        )

        async for raw_member_message in transport.start_streaming_conversation(
            group_channel=group_channel,
            participants=participants,
            muted_participants=muted_participants,
            init_message=msg,
            end_message=end_message,
            timeout=timeout,
        ):
            message = json.loads(raw_member_message.payload.decode("utf-8"))
            yield SendMessageResponse(message)

    async def broadcast_message_streaming(
        request: SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str = None,
        message_limit: int = None,
        timeout: float = 60.0,
    ) -> AsyncIterator[SendMessageResponse]:
        """
        Broadcast a streaming request using the provided transport.
        """
        if not request.id:
            request.id = str(uuid4())

        msg = message_translator(
            request=request.model_dump(mode="json", exclude_none=True)
        )

        if not broadcast_topic:
            broadcast_topic = topic

        # determine how many messages to stream until we break out
        # if none, set strict number of recipients messages
        if message_limit is None:
            message_limit = len(recipients)

        try:
            async for raw_resp in transport.gather_stream(
                broadcast_topic,
                msg,
                recipients=recipients,
                message_limit=message_limit,
                timeout=timeout,
            ):
                try:
                    resp = json.loads(raw_resp.payload.decode("utf-8"))
                    yield SendMessageResponse(resp)
                except Exception as e:
                    logger.error(f"Error decoding JSON response: {e}")
                    continue
        except Exception as e:
            logger.error(
                f"Error gathering streaming A2A request with transport {transport.type()}: {e}"
            )
            return

    async def broadcast_message(
        request: SendMessageRequest | SendStreamingMessageRequest,
        recipients: List[str] | None = None,
        broadcast_topic: str = None,
        timeout: float = 60.0,
    ) -> List[SendMessageResponse]:
        """
        Broadcast a request using the provided transport.
        """
        if not request.id:
            request.id = str(uuid4())

        msg = message_translator(
            request=request.model_dump(mode="json", exclude_none=True)
        )

        if not broadcast_topic:
            broadcast_topic = topic

        try:
            responses = await transport.gather(
                broadcast_topic,
                msg,
                recipients=recipients,
                timeout=timeout,
            )
        except Exception as e:
            logger.error(
                f"Error gathering A2A request with transport {transport.type()}: {e}"
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

    return {
        "start_groupchat": start_groupchat,
        "start_streaming_groupchat": start_streaming_groupchat,
        "broadcast_message": broadcast_message,
        "broadcast_message_streaming": broadcast_message_streaming,
    }
