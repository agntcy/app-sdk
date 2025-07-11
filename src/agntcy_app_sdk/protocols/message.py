# Copyright AGNTCY Contributors (https://github.com/agntcy)
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
        route_path: Optional[str] = None,
        method: Optional[str] = None,
        headers: Optional[dict] = None,
        status_code: Optional[int] = None,
    ):
        self.type = type
        self.payload = payload
        self.reply_to = reply_to
        self.route_path = route_path
        self.method = method
        self.headers = headers if headers is not None else {}
        self.status_code = status_code

    def __repr__(self) -> str:
        return f"Message(type={self.type}, payload={self.payload}, reply_to={self.reply_to}, route_path={self.route_path}, method={self.method}, headers={self.headers}, status_code={self.status_code})"

    def __str__(self) -> str:
        return f"Message(type={self.type}, payload={self.payload}, reply_to={self.reply_to}, route_path={self.route_path}, method={self.method}, headers={self.headers}, status_code={self.status_code})"

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
                payload_bytes = payload_bytes.encode("utf-8")
            else:
                payload_bytes = str(payload_bytes).encode("utf-8")

        # Create a dictionary representation of the Message
        message_dict = {
            "type": self.type,
            "payload": base64.b64encode(payload_bytes).decode("ascii"),
        }

        if self.route_path is not None:
            message_dict["route_path"] = self.route_path
        if self.method is not None:
            message_dict["method"] = self.method
        if self.headers:
            message_dict["headers"] = self.headers
        if self.status_code is not None:
            message_dict["status_code"] = self.status_code
        if self.reply_to is not None:
            message_dict["reply_to"] = self.reply_to

        # Convert dictionary to JSON string and then to bytes
        return json.dumps(message_dict).encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> "Message":
        """
        Deserialize bytes into a Message object.

        Args:
            data: The serialized message bytes

        Returns:
            Message: The deserialized Message object
        """
        # Ensure input is bytes
        if isinstance(data, str):
            data = data.encode("utf-8")

        # Convert bytes to JSON string and then to dictionary
        message_dict = json.loads(data.decode("utf-8"))

        # Extract required fields
        type_value = message_dict.get("type")
        # Decode the base64-encoded payload
        payload = base64.b64decode(message_dict["payload"])

        # Extract optional fields
        reply_to = message_dict.get("reply_to")
        route_path = message_dict.get("route_path")
        method = message_dict.get("method")
        headers = message_dict.get("headers", {})
        status_code = message_dict.get("status_code")

        # Create and return a new Message instance
        return cls(
            type=type_value,
            payload=payload,
            reply_to=reply_to,
            route_path=route_path,
            method=method,
            headers=headers,
            status_code=status_code,
        )
