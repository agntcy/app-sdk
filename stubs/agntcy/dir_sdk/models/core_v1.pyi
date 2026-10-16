# Local type stubs for agntcy-dir 1.0.0 (no py.typed). Copied from the
# generated agntcy/dir/core/v1/record_pb2.pyi, limited to what is used here.

from collections.abc import Mapping

from google.protobuf import message as _message
from google.protobuf import struct_pb2 as _struct_pb2

class RecordRef(_message.Message):
    cid: str
    def __init__(self, cid: str | None = ...) -> None: ...

class Record(_message.Message):
    data: _struct_pb2.Struct
    def __init__(
        self, data: _struct_pb2.Struct | Mapping[str, object] | None = ...
    ) -> None: ...
