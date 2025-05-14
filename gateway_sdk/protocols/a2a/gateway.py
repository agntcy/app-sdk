# Copyright 2025 Cisco Systems, Inc. and its affiliates
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# SPDX-License-Identifier: Apache-2.0

from starlette.types import Scope
from typing import Dict, Any, Callable
import json
import httpx

from a2a.client import A2AClient, A2ACardResolver
from a2a.server import A2AServer
from a2a.types import A2ARequest

from gateway_sdk.protocols.base_protocol import BaseAgentProtocol
from gateway_sdk.transports.base_transport import BaseTransport
from gateway_sdk.protocols.message import Message

from gateway_sdk.common.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

class A2AProtocol(BaseAgentProtocol):
    def get_type(self):
        return "A2A"
    
    async def create_client(self, url, transport: BaseTransport = None, **kwargs) -> A2AClient:
        """
        Create an A2A client, passing in the transport and authentication details.
        """
        httpx_client = httpx.AsyncClient()
        client = await A2AClient.get_client_from_agent_card_url(
            httpx_client, url
        )

        # fix bug in A2AClient.get_client_from_agent_card_url where the card is not being set
        if not hasattr(client, "agent_card"):
            agent_card = await A2ACardResolver(
                httpx_client, base_url=url,
            ).get_agent_card()
            client.agent_card = agent_card

        if transport:
            logger.info(
                f"Using transport {transport.get_type()} for A2A client {client.agent_card.name}"
            )
            topic = f"{agent_card.name}_{agent_card.version}" # TODO: use a method to generate the topic
            transport.bind_to_topic(topic)

            async def _send_request(request: A2ARequest) -> None:
                """
                Send a request using the provided transport.
                """
                response = await transport.publish(
                    topic,
                    self.message_translator(request),
                    respond=True,
                )

                response.payload = json.loads(response.payload.decode('utf-8'))
                return response.payload

            # override the _send_request method to use the provided transport
            client._send_request = _send_request

        return client
    
    def message_translator(self, request: A2ARequest) -> Message:
        """
        Translate an A2A request into a Message object.
        """
        message = Message(
            type="A2ARequest",
            payload=json.dumps(request.root.model_dump(mode='json'))
        )

        return message

    def create_ingress_handler(self, server: A2AServer) -> Callable[[Message], Message]:
        """
        Create a bridge between the A2A server and the ASGI adapter.
        """
        # Create an ASGI adapter
        self._app = server.app()
        return self.handle_incoming_request
    
    async def handle_incoming_request(self, message: Message) -> Message:
        """
        Handle an incoming request and return a response.
        """
        assert self._app is not None, "ASGI app is not set up"

        body = message.payload
        route_path = "/" # json-rpc path
        method = "POST" # A2A json-rpc will always use POST

        # Set up ASGI scope
        scope: Scope = {
            "type": "http",
            'asgi': {'version': '3.0', 'spec_version': '2.1'},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": route_path,
            "raw_path": route_path.encode('utf-8'),
            "query_string": b"",
            "headers": [
                (b"host", b"nats-bridge"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode('utf-8')),
            ],
            "client": ("nats-bridge", 0),
            "server": ("nats-bridge", 0),
        }
        
        # Create the receive channel that will yield request body
        async def receive() -> Dict[str, Any]:
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }
        
        # Create the send channel that will receive responses
        response_data = {
            "status": None,
            "headers": None,
            "body": bytearray(),
        }
        
        async def send(message: Dict[str, Any]) -> None:
            message_type = message["type"]
            
            if message_type == "http.response.start":
                response_data["status"] = message["status"]
                response_data["headers"] = message.get("headers", [])
                
            elif message_type == "http.response.body":
                if "body" in message:
                    response_data["body"].extend(message["body"])
        
        # Call the ASGI application with our scope, receive, and send
        await self._app(scope, receive, send)
        
        # Parse the body
        body = bytes(response_data["body"])
        try:
            body_obj = json.loads(body.decode("utf-8"))
            payload = json.dumps(body_obj).encode("utf-8")  # re-encode as bytes
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = body  # raw bytes

        return Message(
            type="A2AResponse",
            payload=payload,
            reply_to=message.reply_to,
            correlation_id=message.correlation_id,
        )


    
