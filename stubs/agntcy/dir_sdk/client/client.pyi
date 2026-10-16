# Local type stubs for agntcy-dir 1.0.0 (no py.typed).

import builtins
from collections.abc import Sequence

from agntcy.dir_sdk.client.config import Config
from agntcy.dir_sdk.models import core_v1, search_v1

class Client:
    config: Config
    def __init__(self, config: Config | None = None) -> None: ...
    def push(
        self,
        records: builtins.list[core_v1.Record],
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> builtins.list[core_v1.RecordRef]: ...
    def pull(
        self,
        refs: builtins.list[core_v1.RecordRef],
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> builtins.list[core_v1.Record]: ...
    def delete(
        self,
        refs: builtins.list[core_v1.RecordRef],
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> None: ...
    def search_records(
        self,
        req: search_v1.SearchRecordsRequest,
        metadata: Sequence[tuple[str, str]] | None = None,
    ) -> builtins.list[search_v1.SearchRecordsResponse]: ...
