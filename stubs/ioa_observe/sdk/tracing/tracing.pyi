# Local type stubs for ioa-observe-sdk 1.0.49 (no py.typed).

from contextlib import AbstractContextManager

from opentelemetry.trace import Tracer

class TracerWrapper:
    instance: TracerWrapper
    app_name: str | None
    def __new__(cls) -> TracerWrapper: ...
    def get_tracer(self) -> Tracer: ...

def session_start(
    apply_transform: bool = False,
) -> AbstractContextManager[dict[str, str | None]]: ...
