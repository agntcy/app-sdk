# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any, Dict, Optional
from agntcy_app_sdk.protocols.message import Message
from agntcy_app_sdk.transports.base import ResponseMode


class MCPClient:
    def __init__(self, transport, session_id: str, topic: str, route_path: str = "/"):
        self.transport = transport
        self.session_id = session_id
        self.topic = topic
        self.route_path = route_path

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.transport:
            try:
                await self.transport.close()
            except Exception as e:
                print(f"[error] Transport close failed: {e}")

    def _build_message(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        request_id: int = 1,
    ) -> Message:
        if headers is None:
            headers = {}
        headers.setdefault("Mcp-Session-Id", self.session_id)
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        return Message(
            type="MCPRequest",
            payload=json.dumps(payload),
            route_path=self.route_path,
            method="POST",
            headers=headers,
        )

    async def call_mcp_method(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
        request_id: int = 1,
    ) -> Dict[str, Any]:
        try:
            message = self._build_message(method, params, headers, request_id)
            response = await self.transport.request(
                self.topic, message, response_mode=ResponseMode.FIRST
            )
            data = json.loads(response.payload.decode("utf-8"))
            if "error" in data:
                raise RuntimeError(f"[MCP Error] {method} failed: {data['error']}")
            return data["result"]
        except Exception as e:
            print(f"[error] Failed MCP method '{method}': {e}")
            raise

    # Specific wrappers
    async def call_tool(
        self, name: str, arguments: Dict[str, Any], request_id: int = 1
    ) -> dict:
        return await self.call_mcp_method(
            "tools/call", {"name": name, "arguments": arguments}, request_id=request_id
        )

    async def list_tools(self, request_id: int = 1) -> dict:
        return await self.call_mcp_method("tools/list", request_id=request_id)

    async def list_resources(self, request_id: int = 1) -> dict:
        return await self.call_mcp_method("resources/list", request_id=request_id)

    async def list_resource_templates(self, request_id: int = 1) -> dict:
        return await self.call_mcp_method(
            "resources/templates/list", request_id=request_id
        )

    async def read_resource(self, uri: str, request_id: int = 1) -> dict:
        return await self.call_mcp_method(
            "resources/read", {"uri": uri}, request_id=request_id
        )

    async def list_prompts(self, request_id: int = 1) -> dict:
        return await self.call_mcp_method("prompts/list", request_id=request_id)

    async def get_prompt(
        self, name: str, arguments: Optional[Dict[str, Any]] = None, request_id: int = 1
    ) -> dict:
        return await self.call_mcp_method(
            "prompts/get",
            {"name": name, "arguments": arguments or {}},
            request_id=request_id,
        )

    async def complete(
        self,
        ref: Dict[str, Any],
        argument: Dict[str, str],
        request_id: int = 1,
    ) -> dict:
        raise NotImplementedError(
            "The 'complete' method is not supported in this MCPClient implementation."
        )
