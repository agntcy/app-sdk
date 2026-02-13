# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)

from agntcy_app_sdk.common.auth import is_identity_auth_enabled
from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.semantic.a2a.client.utils import (
    get_identity_auth_error,
    message_translator,
)
from agntcy_app_sdk.transport.base import BaseTransport

if TYPE_CHECKING:
    from agntcy_app_sdk.semantic.a2a.client.config import ClientConfig

configure_logging()
logger = get_logger(__name__)

# Recognized URI schemes for patterns transports
_PATTERNS_SCHEMES = {"slim", "nats"}


def _parse_topic_from_url(url: str) -> str:
    """Extract a topic from a scheme-encoded URL.

    Examples::

        "slim://Hello_World_Agent_1.0.0"  →  "Hello_World_Agent_1.0.0"
        "nats://my_topic"                 →  "my_topic"
        "Hello_World_Agent_1.0.0"         →  "Hello_World_Agent_1.0.0"
    """
    if "://" in url:
        scheme, _, rest = url.partition("://")
        if scheme.lower() in _PATTERNS_SCHEMES:
            return rest
    return url


class PatternsClientTransport(ClientTransport):
    """Adapts a ``BaseTransport`` (SLIM-patterns / NATS-patterns) to the
    upstream ``a2a.client.transports.base.ClientTransport`` interface.

    This lets the upstream ``ClientFactory`` treat SLIM/NATS transports the
    same way it treats ``JsonRpcTransport`` or ``GrpcTransport``.

    Standard A2A operations (``send_message``, ``get_task``, …) are routed
    through the transport's ``request()`` method using the internal
    ``Message`` wire format.  Streaming falls back to ``send_message``
    (patterns transports are request/reply).
    """

    def __init__(
        self,
        transport: BaseTransport,
        agent_card: AgentCard,
        topic: str,
    ) -> None:
        self._transport = transport
        self._agent_card = agent_card
        self._topic = topic

    # ------------------------------------------------------------------
    # Factory method — matches ``TransportProducer`` signature
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        card: AgentCard,
        url: str,
        config: ClientConfig,
        interceptors: list[ClientCallInterceptor],
    ) -> PatternsClientTransport:
        """``TransportProducer`` compatible factory for upstream
        ``ClientFactory.register()``.

        The ``url`` parameter comes from the agent card (``card.url`` or
        an ``additional_interfaces`` entry) and is expected to be a
        scheme-encoded topic, e.g. ``slim://my_topic`` or
        ``nats://my_topic``.  The scheme determines which transport
        factory callable to use; the path is the topic.
        """
        # Parse topic from scheme-encoded URL (e.g. "slim://my_topic")
        topic = _parse_topic_from_url(url)
        transport_label = card.preferred_transport or url

        base_transport: BaseTransport | None = None
        if "slim" in str(transport_label).lower():
            if config.slim_patterns_transport_factory is not None:
                base_transport = config.slim_patterns_transport_factory()
            else:
                raise ValueError(
                    "slim_patterns_transport_factory is required in ClientConfig "
                    "for slimpatterns transport"
                )
        elif "nats" in str(transport_label).lower():
            if config.nats_transport_factory is not None:
                base_transport = config.nats_transport_factory()
            else:
                raise ValueError(
                    "nats_transport_factory is required in ClientConfig "
                    "for natspatterns transport"
                )
        else:
            raise ValueError(
                f"PatternsClientTransport cannot handle transport label "
                f"'{transport_label}'; expected 'slimpatterns' or 'natspatterns'."
            )

        return cls(base_transport, card, topic)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_rpc(self, rpc_payload: dict) -> dict:
        """Send an A2A JSON-RPC payload through the underlying transport."""
        headers: dict[str, str] = {}

        if is_identity_auth_enabled():
            try:
                from identityservice.sdk import IdentityServiceSdk

                access_token = IdentityServiceSdk().access_token()
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
            except Exception as e:
                logger.error("Failed to get access token for agent: %s", e)

        try:
            response = await self._transport.request(
                self._topic,
                message_translator(request=rpc_payload, headers=headers),
            )
            response_payload = json.loads(response.payload.decode("utf-8"))

            # Handle Identity-Middleware auth errors
            if (
                response_payload.get("error") == "forbidden"
                or response.status_code == 403
            ):
                logger.error(
                    "Received forbidden error in A2A response due to identity auth"
                )
                return get_identity_auth_error()

            return response_payload
        except Exception as e:
            logger.error(
                "Error sending A2A request with transport %s: %s",
                self._transport.type(),
                e,
            )
            raise

    # ------------------------------------------------------------------
    # ClientTransport interface
    # ------------------------------------------------------------------

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task | Message:
        """Send a non-streaming message and return the result."""
        from uuid import uuid4

        from a2a.types import SendMessageRequest

        rpc_request = SendMessageRequest(id=str(uuid4()), params=request)
        rpc_payload = rpc_request.model_dump(mode="json", exclude_none=True)
        response = await self._send_rpc(rpc_payload)

        # Parse result from JSON-RPC response
        result = response.get("result", response)
        if isinstance(result, dict):
            if result.get("kind") == "task" or "status" in result:
                return Task.model_validate(result)
            return Message.model_validate(result)
        return Message.model_validate(response)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent, None
    ]:
        """Streaming falls back to a single send_message for patterns
        transports (they are inherently request/reply)."""
        result = await self.send_message(
            request, context=context, extensions=extensions
        )
        yield result

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieve a task by ID."""
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/get",
            "params": request.model_dump(mode="json", exclude_none=True),
        }
        response = await self._send_rpc(rpc_payload)
        return Task.model_validate(response.get("result", response))

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Cancel a task by ID."""
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/cancel",
            "params": request.model_dump(mode="json", exclude_none=True),
        }
        response = await self._send_rpc(rpc_payload)
        return Task.model_validate(response.get("result", response))

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Set push notification config for a task."""
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/pushNotificationConfig/set",
            "params": request.model_dump(mode="json", exclude_none=True),
        }
        response = await self._send_rpc(rpc_payload)
        return TaskPushNotificationConfig.model_validate(
            response.get("result", response)
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Get push notification config for a task."""
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": "1",
            "method": "tasks/pushNotificationConfig/get",
            "params": request.model_dump(mode="json", exclude_none=True),
        }
        response = await self._send_rpc(rpc_payload)
        return TaskPushNotificationConfig.model_validate(
            response.get("result", response)
        )

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent, None
    ]:
        """Resubscribe to task updates — not supported by patterns transports."""
        raise NotImplementedError("resubscribe is not supported by patterns transports")
        # Make the method a valid async generator
        yield  # pragma: no cover

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AgentCard:
        """Return the locally-cached agent card."""
        return self._agent_card

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._transport.close()
