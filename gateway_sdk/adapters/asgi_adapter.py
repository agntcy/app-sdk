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

from .base import RequestHandler
from starlette.applications import Starlette
from ..message import Message, Response
from urllib.parse import urlparse, urlencode
from typing import Dict, Any, Optional
import json

class AsgiHandler(RequestHandler):
    """ASGI implementation of RequestHandler."""
    
    def __init__(self, app: Starlette):
        self.app = app
        self._started = False

    async def start(self) -> None:
        """Start the ASGI handler."""
        self._started = True
    
    async def handle_incoming_request(self, message: Message) -> Response:
        print(f"Handling incoming request: {message.path} with method {message.method}")
        """Handle a request by passing it to the ASGI application."""
        # Ensure path starts with a leading slash
        path = message.path if message.path.startswith('/') else f'/{message.path}'
        
        # Prepare body data
        body_bytes = json.dumps(message.data).encode() if message.data else b''
        
        # Convert headers to ASGI format (lowercase, byte strings)
        asgi_headers = [
            (k.lower().encode(), v.encode())
            for k, v in message.headers.items()
        ]
        
        # Add content-type if not present
        content_type_found = any(k == b'content-type' for k, _ in asgi_headers)
        if not content_type_found and message.data:
            asgi_headers.append((b'content-type', b'application/json'))
        
        # Create ASGI scope
        scope = {
            'type': 'http',
            'asgi': {'version': '3.0', 'spec_version': '2.1'},
            'http_version': '1.1',
            'method': message.method,
            'scheme': 'http',
            'path': path,
            'raw_path': path.encode(),
            'query_string': b'',
            'headers': asgi_headers,
            'client': ('message-bridge', 0),
            'server': ('message-bridge', 0),
        }
        
        # Create response capture for ASGI
        response_body = []
        response_status = [None]
        response_headers = [[]]
        
        # Define ASGI receive function (provides request body)
        async def receive():
            return {
                'type': 'http.request',
                'body': body_bytes,
                'more_body': False
            }
        
        # Define ASGI send function (captures response)
        async def send(message):
            if message['type'] == 'http.response.start':
                response_status[0] = message.get('status', 200)
                response_headers[0] = message.get('headers', [])
            elif message['type'] == 'http.response.body':
                response_body.append(message.get('body', b''))
        
        # Process the request through the ASGI application
        print(f"Processing ASGI request: {path} with method {message.method}")
        await self.app(scope, receive, send)
        
        # Create response object
        full_response_body = b''.join(response_body)
        
        # Extract content-type from response headers
        content_type = None
        headers_dict = {}
        for header_name, header_value in response_headers[0]:
            header_key = header_name.decode().lower()
            header_val = header_value.decode()
            headers_dict[header_key] = header_val
            if header_key == 'content-type':
                content_type = header_val
        
        # Try to parse response body based on content type
        response_data = None
        if content_type and 'application/json' in content_type:
            try:
                response_data = json.loads(full_response_body.decode())
            except:
                response_data = full_response_body
        else:
            response_data = full_response_body

        print(f"ASGI response status: {response_status[0]}, body: {response_data}, headers: {headers_dict}")
        
        return Response(
            status_code=response_status[0] or 200,
            body=response_data,
            headers=headers_dict,
            correlation_id=message.reply_to
        )
    
    @staticmethod
    def translate_incoming_request(message: Message) -> Dict[str, Any]:
        """Translate an incoming ASGI request to a dictionary."""
    
    @staticmethod
    def translate_outgoing_request(
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None,
        reply_to: Optional[str] = None
    ) -> Message:
        """Construct a Message from HTTP-style input."""
        parsed_url = urlparse(url)
        path = parsed_url.path

        if method.upper() == "GET" and params:
            query_string = urlencode(params, doseq=True)
            path = f"{path}?{query_string}"
        elif parsed_url.query:
            path = f"{path}?{parsed_url.query}"

        print(f"Translating outgoing request: {method} {path} with params {params} and json {json}")

        return Message(
            path=path,
            method=method.upper(),
            data=json if method.upper() != "GET" else None,
            headers=headers,
            correlation_id=correlation_id,
            reply_to=reply_to
        )