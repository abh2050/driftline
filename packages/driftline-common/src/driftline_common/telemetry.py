"""OpenTelemetry setup shared by every Driftline service (spec 2.7, section 8).

All signals go over OTLP/HTTP to otel-collector (never directly to Azure Monitor).
Metrics use delta temporality, which Application Insights expects.
Trace context travels through Service Bus as W3C `traceparent`/`tracestate` application
properties via `inject_context` / `extract_context`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, MutableMapping
from typing import Any

from opentelemetry import context, metrics, propagate, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import (
    Counter,
    Histogram,
    MeterProvider,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import AggregationTemporality, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
DEFAULT_ENDPOINT = "http://otel-collector"

_DELTA: dict[type, AggregationTemporality] = {
    Counter: AggregationTemporality.DELTA,
    UpDownCounter: AggregationTemporality.CUMULATIVE,
    Histogram: AggregationTemporality.DELTA,
    ObservableCounter: AggregationTemporality.DELTA,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge: AggregationTemporality.CUMULATIVE,
}


class Telemetry:
    """Handles to the configured providers, so callers can flush on shutdown."""

    def __init__(
        self,
        tracer_provider: TracerProvider,
        meter_provider: MeterProvider,
        logger_provider: LoggerProvider,
    ) -> None:
        self.tracer_provider = tracer_provider
        self.meter_provider = meter_provider
        self.logger_provider = logger_provider

    def shutdown(self) -> None:
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()
        self.logger_provider.shutdown()


def setup_telemetry(
    service_name: str, *, endpoint: str | None = None, attributes: Mapping[str, Any] | None = None
) -> Telemetry:
    base = (endpoint or os.environ.get(ENDPOINT_ENV) or DEFAULT_ENDPOINT).rstrip("/")
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.namespace": "driftline",
            "deployment.environment": os.environ.get("AZURE_ENV_NAME", "local"),
            **(attributes or {}),
        }
    )
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tp)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics", preferred_temporality=_DELTA),
        export_interval_millis=30_000,
    )
    mp = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(mp)

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs"))
    )
    set_logger_provider(lp)
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))
    return Telemetry(tp, mp, lp)


def inject_context(carrier: MutableMapping[str, str] | None = None) -> MutableMapping[str, str]:
    """Write the current trace context into message properties (W3C traceparent)."""
    out: MutableMapping[str, str] = carrier if carrier is not None else {}
    propagate.inject(out)
    return out


def extract_context(carrier: Mapping[str, Any]) -> context.Context:
    """Read trace context from message properties, tolerating bytes values from AMQP."""
    clean = {
        (k.decode() if isinstance(k, bytes) else str(k)): (
            v.decode() if isinstance(v, bytes) else str(v)
        )
        for k, v in carrier.items()
    }
    return propagate.extract(clean)
