# Local type stubs for ioa-observe-sdk 1.0.49, which ships no py.typed
# marker. Only the API this repository uses is declared.

from opentelemetry.propagators.textmap import TextMapPropagator
from opentelemetry.sdk._logs.export import LogExporter
from opentelemetry.sdk.metrics.export import MetricExporter
from opentelemetry.sdk.trace import SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter

class Observe:
    @staticmethod
    def init(
        app_name: str = ...,
        api_endpoint: str = ...,
        api_key: str | None = None,
        enabled: bool = True,
        headers: dict[str, str] = ...,
        disable_batch: bool = False,
        telemetry_enabled: bool = True,
        exporter: SpanExporter | None = None,
        metrics_exporter: MetricExporter | None = None,
        metrics_headers: dict[str, str] | None = None,
        logging_exporter: LogExporter | None = None,
        logging_headers: dict[str, str] | None = None,
        processor: SpanProcessor | None = None,
        propagator: TextMapPropagator | None = None,
        observe_sync_enabled: bool = False,
        should_enrich_metrics: bool = True,
        realtime_observability_enabled: bool | None = None,
        resource_attributes: dict[str, object] = ...,
        instruments: set[object] | None = None,
        block_instruments: set[object] | None = None,
        image_uploader: object | None = None,
    ) -> object | None: ...
