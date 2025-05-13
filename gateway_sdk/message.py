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

from typing import Optional, Any, Dict, Union

# =============== Message Models ================

class Message:
    """Base message structure for communication between components."""
    
    def __init__(
        self,
        path: str,
        method: str = "POST",
        data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None,
        reply_to: Optional[str] = None
    ):
        self.path = path
        self.method = method
        self.data = data or {}
        self.headers = headers or {}
        self.correlation_id = correlation_id
        self.reply_to = reply_to
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert message to dictionary representation."""
        return {
            "path": self.path,
            "method": self.method,
            "data": self.data,
            "headers": self.headers,
            "correlation_id": self.correlation_id,
            "reply_to": self.reply_to
        }
        
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Message':
        """Create message from dictionary representation."""
        return cls(
            path=data.get("path") or data.get("route", ""),
            method=data.get("method", "POST"),
            data=data.get("data", {}),
            headers=data.get("headers", {}),
            correlation_id=data.get("correlation_id"),
            reply_to=data.get("reply_to")
        )


class Response:
    """Response structure for communication between components."""
    
    def __init__(
        self,
        status_code: int = 200,
        body: Union[Dict[str, Any], bytes, str] = None,
        headers: Optional[Dict[str, str]] = None,
        correlation_id: Optional[str] = None
    ):
        self.status_code = status_code
        self.body = body if body is not None else {}
        self.headers = headers or {}
        self.correlation_id = correlation_id
        
    def to_dict(self) -> Dict[str, Any]:
        """Convert response to dictionary representation."""
        body_data = self.body
        if isinstance(self.body, bytes):
            body_data = self.body.decode('utf-8', errors='replace')
        elif not isinstance(self.body, (dict, str)):
            body_data = str(self.body)
            
        return {
            "status_code": self.status_code,
            "body": body_data,
            "headers": self.headers,
            "correlation_id": self.correlation_id
        }
