# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

from typing import Optional
import grpc
import json
from google.protobuf.struct_pb2 import Struct
from agntcy.dir_sdk.models import core_v1

from agntcy.oasfsdk.validation.v1.validation_service_pb2 import ValidateRecordRequest
from agntcy.oasfsdk.validation.v1.validation_service_pb2_grpc import (
    ValidationServiceStub,
)
from agntcy.oasfsdk.translation.v1.translation_service_pb2 import (
    A2AToRecordRequest,
    RecordToA2ARequest,
)
from agntcy.oasfsdk.translation.v1.translation_service_pb2_grpc import (
    TranslationServiceStub,
)

from a2a.types import (
    AgentCard,
)


class SemanticTranslator:
    """
    Handles translation and validation between agent semantic protocols such as
    A2A, MCP, and OASF record formats.

    Translation and validation is performed by the oasf-sdk gRPC service.
    """

    def __init__(
        self, host: str = "localhost", port: int = 31234, auto_connect: bool = True
    ):
        """
        Initialize the SemanticTranslator.

        Args:
            host: gRPC server host
            port: gRPC server port
            auto_connect: If True, establishes connection immediately.
                         If False, call connect() manually.
        """
        self.address = f"{host}:{port}"
        self._channel: Optional[grpc.Channel] = None
        self._translation_stub: Optional[TranslationServiceStub] = None
        self._validation_stub: Optional[ValidationServiceStub] = None
        self._managed_context = False

        if auto_connect:
            self.connect()

    def connect(self) -> None:
        """Establish gRPC connection and initialize stubs."""
        if self._channel is None:
            self._channel = grpc.insecure_channel(self.address)
            self._translation_stub = TranslationServiceStub(self._channel)
            self._validation_stub = ValidationServiceStub(self._channel)

    def close(self) -> None:
        """Close gRPC connection and cleanup resources."""
        if self._channel:
            self._channel.close()
            self._channel = None
            self._translation_stub = None
            self._validation_stub = None

    def __enter__(self):
        """Context manager entry - establishes gRPC connection."""
        self._managed_context = True
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - closes gRPC connection."""
        if self._managed_context:
            self.close()
            self._managed_context = False

    def __del__(self):
        """Cleanup on garbage collection."""
        self.close()

    def _oafs_sdk_record_to_dir_sdk_record(
        self, record_struct: Struct
    ) -> core_v1.Record:
        """
        Convert OASF record Struct to core_v1.Record.

        Args:
            record_struct: Protobuf Struct representation of the OASF record

        Returns:
            core_v1.Record instance
        """
        record_json = json.dumps(dict(record_struct))
        record = core_v1.Record.model_validate_json(record_json)
        return record

    def from_oasf_record_data(self, record_data: dict) -> core_v1.Record:
        """
        Convert OASF record data dictionary to core_v1.Record.

        Args:
            record_data: Dictionary representation of the OASF record

        Returns:
            core_v1.Record instance
        """
        record_json = json.dumps(record_data)
        record = core_v1.Record.model_validate_json(record_json)
        return record

    def validate_oasf(self, record_data: dict) -> tuple[bool, list[str]]:
        """
        Validate an OASF record.

        Args:
            record_data: Dictionary containing the OASF record to validate

        Returns:
            Tuple of (is_valid, errors) where errors is a list of error messages

        Raises:
            RuntimeError: If not connected
            grpc.RpcError: If the gRPC call fails
        """
        if not self._validation_stub:
            raise RuntimeError(
                "Not connected. Call connect() or use as context manager."
            )

        record_struct = Struct()
        record_struct.update(record_data)

        request = ValidateRecordRequest(record=record_struct)
        response = self._validation_stub.ValidateRecord(request)

        return response.is_valid, list(response.errors)

    def a2a_to_oasf(self, agent_card: AgentCard) -> Optional[Struct]:
        """
        Translate an A2A AgentCard to an OASF record.

        Args:
            agent_card: The A2A AgentCard to translate

        Returns:
            Protobuf Struct containing the OASF record, or None if translation fails

        Raises:
            RuntimeError: If not connected
            grpc.RpcError: If the gRPC call fails
        """
        if not self._translation_stub:
            raise RuntimeError(
                "Not connected. Call connect() or use as context manager."
            )

        dict_agent_card = json.loads(agent_card.model_dump_json())
        data = {"a2aCard": dict_agent_card}

        record_struct = Struct()
        record_struct.update(data)

        request = A2AToRecordRequest(data=record_struct)
        response = self._translation_stub.A2AToRecord(request)

        return response.record

    def oasf_to_a2a(self, record: dict) -> str:
        """
        Translate an OASF record to an A2A Card.

        Args:
            record: Dictionary containing the OASF record

        Returns:
            String containing the A2A Card

        Raises:
            RuntimeError: If not connected
            grpc.RpcError: If the gRPC call fails
        """
        if not self._translation_stub:
            raise RuntimeError(
                "Not connected. Call connect() or use as context manager."
            )

        record_struct = Struct()
        record_struct.update(record)

        request = RecordToA2ARequest(record=record_struct)
        response = self._translation_stub.RecordToA2A(request)

        return response
