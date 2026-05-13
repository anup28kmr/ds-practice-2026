"""OpenTelemetry helper shared by every CP4-instrumented service.

Single call: ``tracer, meter = init_telemetry("orchestrator")``.

Exporters use OTLP HTTP against the ``observability`` compose service
(``http://observability:4318``) by default; override with
``OTEL_EXPORTER_OTLP_ENDPOINT``. Set ``OTEL_SDK_DISABLED=1`` (or unset
``OTEL_EXPORTER_OTLP_ENDPOINT`` to an empty string) to disable export so
unit tests and offline runs do not block on a missing collector.
"""

import os
import socket
from typing import Optional, Tuple

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
    OTLPMetricExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_DEFAULT_ENDPOINT = "http://observability:4318"
_initialized = False


def _resource(service_name: str) -> Resource:
    return Resource.create(
        {
            "service.name": service_name,
            "service.instance.id": os.getenv("HOSTNAME", socket.gethostname()),
        }
    )


def _exporter_endpoint() -> Optional[str]:
    if os.getenv("OTEL_SDK_DISABLED", "").lower() in ("1", "true", "yes"):
        return None
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", _DEFAULT_ENDPOINT).strip()
    return endpoint or None


def init_telemetry(service_name: str) -> Tuple[trace.Tracer, metrics.Meter]:
    """Initialize providers exactly once per process; return tracer + meter."""
    global _initialized
    if not _initialized:
        endpoint = _exporter_endpoint()
        resource = _resource(service_name)

        tracer_provider = TracerProvider(resource=resource)
        if endpoint:
            tracer_provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
                )
            )
        trace.set_tracer_provider(tracer_provider)

        readers = []
        if endpoint:
            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
                    export_interval_millis=int(
                        os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "10000")
                    ),
                )
            )
        meter_provider = MeterProvider(resource=resource, metric_readers=readers)
        metrics.set_meter_provider(meter_provider)

        _initialized = True
        print(
            f"[TELEMETRY] service={service_name} "
            f"otel_endpoint={endpoint or 'DISABLED'}"
        )

    return trace.get_tracer(service_name), metrics.get_meter(service_name)
