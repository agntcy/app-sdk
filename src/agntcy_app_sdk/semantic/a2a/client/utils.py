# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any
from uuid import uuid4

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.interceptors import AfterArgs, BeforeArgs, ClientCallInterceptor
from a2a.types import AgentCard
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP

from agntcy_app_sdk.semantic.message import Message
from agntcy_app_sdk.common.logging_config import get_logger

logger = get_logger(__name__)

# JSON-RPC error code -> A2A error class (inverse of upstream's map).
_CODE_TO_ERROR = {code: err for err, code in JSON_RPC_ERROR_CODE_MAP.items()}


def message_translator(
    request: dict[str, Any], headers: dict[str, Any] | None = None
) -> Message:
    """
    Translate an A2A request into the internal Message object.
    """
    if headers is None:
        headers = {}
    if not isinstance(headers, dict):
        raise ValueError("Headers must be a dictionary")

    message = Message(
        type="A2ARequest",
        payload=json.dumps(request),
        route_path="/",  # json-rpc path
        method="POST",  # A2A json-rpc will always use POST
        headers=headers,
    )
    return message


def get_identity_auth_error() -> dict[str, Any]:
    """
    Generate a standard identity authentication error response.

    The body is a JSON-RPC success response carrying an agent ``Message`` in
    the A2A v1 ProtoJSON shape (``result.message``), so callers can parse it
    exactly like a normal ``SendMessage`` reply.
    """
    return {
        "id": str(uuid4()),
        "jsonrpc": "2.0",
        "result": {
            "message": {
                "messageId": str(uuid4()),
                "metadata": {"name": "None"},
                "parts": [{"text": "Access Forbidden. Please check permissions."}],
                "role": "ROLE_AGENT",
            }
        },
    }


def create_rpc_error(error: Any) -> Exception:
    """Build the client-side exception for a JSON-RPC ``error`` object.

    Mirrors the upstream JSON-RPC transport: known A2A error codes map to
    their dedicated ``A2AError`` subclass, anything else becomes an
    :class:`~a2a.client.errors.A2AClientError`.
    """
    if not isinstance(error, dict):
        return A2AClientError(f"Server error: {error}")

    code = error.get("code")
    message = error.get("message", str(error))
    error_cls = _CODE_TO_ERROR.get(code) if isinstance(code, int) else None
    if error_cls is not None:
        return error_cls(message)
    return A2AClientError(f"JSON-RPC Error {code}: {message}")


async def run_before_interceptors(
    interceptors: list[ClientCallInterceptor],
    *,
    method: str,
    request: Any,
    agent_card: AgentCard,
    context: ClientCallContext | None,
) -> tuple[Any, ClientCallContext | None]:
    """Run ``before`` hooks for operations that bypass the upstream ``BaseClient``.

    The upstream client applies interceptors itself for the standard A2A
    operations.  Experimental operations (broadcast, group chat) talk to the
    transport directly, so they apply the same hooks here.  Interceptors may
    replace the request and/or populate ``context.service_parameters`` (which
    the patterns transport forwards as message headers).
    """
    args = BeforeArgs(
        input=request, method=method, agent_card=agent_card, context=context
    )
    for interceptor in interceptors:
        await interceptor.before(args)
    return args.input, args.context


async def run_after_interceptors(
    interceptors: list[ClientCallInterceptor],
    *,
    method: str,
    result: Any,
    agent_card: AgentCard,
    context: ClientCallContext | None,
) -> Any:
    """Run ``after`` hooks for an experimental-operation result."""
    args = AfterArgs(
        result=result, method=method, agent_card=agent_card, context=context
    )
    for interceptor in interceptors:
        await interceptor.after(args)
    return args.result
