# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import grpc
from google.protobuf.struct_pb2 import Struct

from agntcy.oasfsdk.validation.v1.validation_service_pb2 import ValidateRecordRequest
from agntcy.oasfsdk.validation.v1.validation_service_pb2_grpc import (
    ValidationServiceStub,
)
from agntcy.oasfsdk.translation.v1.translation_service_pb2 import (
    RecordToA2ARequest,
)
from agntcy.oasfsdk.translation.v1.translation_service_pb2_grpc import (
    TranslationServiceStub,
)

from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
)

record_data = {
    "name": "example.org/my-agent",
    "schema_version": "0.7.0",
    "version": "v1.0.0",
    "description": "An example agent for demonstration",
    "authors": ["Your Name <your.email@example.com>"],
    "created_at": "2025-01-01T00:00:00Z",
    "domains": [{"id": 101, "name": "technology/internet_of_things"}],
    "locators": [{"type": "docker_image", "url": "ghcr.io/example/my-agent:latest"}],
    "skills": [
        {
            "name": "natural_language_processing/natural_language_understanding",
            "id": 101,
        }
    ],
}

skill = AgentSkill(
    id="hello_world",
    name="Returns hello world",
    description="just returns hello world",
    tags=["hello world"],
    examples=["hi", "hello world"],
)

agent_card = AgentCard(
    name="Hello World Agent",
    description="Just a hello world agent",
    url="http://localhost:9999/",
    version="1.0.0",
    defaultInputModes=["text"],
    defaultOutputModes=["text"],
    capabilities=AgentCapabilities(streaming=True),
    skills=[skill],  # Only the basic skill for the public card
    supportsAuthenticatedExtendedCard=False,
)


def validate_record():
    # Sample OASF record to validate

    # Create gRPC channel
    with grpc.insecure_channel("localhost:31234") as channel:
        stub = ValidationServiceStub(channel)

        # Convert dict to protobuf Struct
        record_struct = Struct()
        record_struct.update(record_data)

        # Create validation request
        request = ValidateRecordRequest(
            record=record_struct
            # schema_url="https://schema.oasf.outshift.com/schema/0.7.0/objects/record"  # Optional
        )

        try:
            # Call validation service
            response = stub.ValidateRecord(request)

            # Print results
            print(f"Valid: {response.is_valid}")
            if response.errors:
                print("Errors:")
                for error in response.errors:
                    print(f"  - {error}")
            else:
                print("No validation errors found!")

        except grpc.RpcError as e:
            print(f"gRPC error: {e.code()}: {e.details()}")


def translate_a2a_to_record():
    with grpc.insecure_channel("localhost:31234") as channel:
        stub = TranslationServiceStub(channel)

        record_struct = Struct()
        record_struct.update(agent_card)

        # Create translation request
        # request = A2AToRecordRequest(
        #    data=agent_card,
        # )


def translate_record_to_a2a():
    with grpc.insecure_channel("localhost:31234") as channel:
        stub = TranslationServiceStub(channel)

        # Convert dict to protobuf Struct
        record_struct = Struct()
        record_struct.update(record_data)

        # Create translation request
        request = RecordToA2ARequest(record=record_struct)

        try:
            # Call translation service
            response = stub.RecordToA2A(request)

            # Print results
            print("A2A Message:")
            print(response.a2a_message)
        except grpc.RpcError as e:
            print(f"gRPC error: {e.code()}: {e.details()}")


if __name__ == "__main__":
    validate_record()
    translate_record_to_a2a()
