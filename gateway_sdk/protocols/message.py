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

from typing import Optional
import json
import base64

# =============== Message Models ================

class Message:
    """Base message structure for communication between components."""
    
    def __init__(
        self,
        type: str,
        payload: bytes,
        reply_to: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        self.type = type
        self.payload = payload
        self.reply_to = reply_to
        self.correlation_id = correlation_id
    
    def serialize(self) -> bytes:
        """
        Serialize the Message object into bytes.
        
        Returns:
            bytes: The serialized message
        """
        # Ensure payload is bytes-like
        payload_bytes = self.payload
        if not isinstance(payload_bytes, bytes):
            if isinstance(payload_bytes, str):
                payload_bytes = payload_bytes.encode('utf-8')
            else:
                payload_bytes = str(payload_bytes).encode('utf-8')
        
        # Create a dictionary representation of the Message
        message_dict = {
            "type": self.type,
            "payload": base64.b64encode(payload_bytes).decode('ascii'),
        }
        
        # Add optional fields only if they exist
        if self.reply_to is not None:
            message_dict["reply_to"] = self.reply_to
            
        if self.correlation_id is not None:
            message_dict["correlation_id"] = self.correlation_id
        
        # Convert dictionary to JSON string and then to bytes
        return json.dumps(message_dict).encode('utf-8')
    
    @classmethod
    def deserialize(cls, data: bytes) -> 'Message':
        """
        Deserialize bytes into a Message object.
        
        Args:
            data: The serialized message bytes
            
        Returns:
            Message: The deserialized Message object
        """
        # Ensure input is bytes
        if isinstance(data, str):
            data = data.encode('utf-8')
            
        # Convert bytes to JSON string and then to dictionary
        message_dict = json.loads(data.decode('utf-8'))
        
        # Extract required fields
        type_value = message_dict.get("type")
        # Decode the base64-encoded payload
        payload = base64.b64decode(message_dict["payload"])
        
        # Extract optional fields
        reply_to = message_dict.get("reply_to")
        correlation_id = message_dict.get("correlation_id")
        
        # Create and return a new Message instance
        return cls(
            type=type_value,
            payload=payload,
            reply_to=reply_to,
            correlation_id=correlation_id
        )