# Local type stubs for agntcy-dir 1.0.0 (no py.typed). Copied from the
# generated agntcy/dir/search/v1/*_pb2.pyi, limited to what is used here.

from collections.abc import Iterable, Mapping
from typing import ClassVar

from google.protobuf import message as _message
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper

from agntcy.dir_sdk.models.core_v1 import Record

class RecordQueryType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    RECORD_QUERY_TYPE_UNSPECIFIED: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_NAME: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_VERSION: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_SKILL_ID: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_SKILL_NAME: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_LOCATOR: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_MODULE_NAME: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_DOMAIN_ID: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_DOMAIN_NAME: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_CREATED_AT: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_AUTHOR: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_SCHEMA_VERSION: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_MODULE_ID: ClassVar[RecordQueryType]
    RECORD_QUERY_TYPE_VERIFIED: ClassVar[RecordQueryType]

class RecordQuery(_message.Message):
    type: RecordQueryType
    value: str
    def __init__(
        self, type: RecordQueryType | str | None = ..., value: str | None = ...
    ) -> None: ...

class SearchRecordsRequest(_message.Message):
    queries: _containers.RepeatedCompositeFieldContainer[RecordQuery]
    limit: int
    offset: int
    def __init__(
        self,
        queries: Iterable[RecordQuery | Mapping[str, object]] | None = ...,
        limit: int | None = ...,
        offset: int | None = ...,
    ) -> None: ...

class SearchRecordsResponse(_message.Message):
    record: Record
    def __init__(self, record: Record | Mapping[str, object] | None = ...) -> None: ...
