from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable
from uuid import uuid4

from fastapi import Request  # type: ignore

try:
    from opentelemetry import metrics, trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:  # pragma: no cover - optional until observability dependencies are installed.
    metrics = None  # type: ignore
    trace = None  # type: ignore
    FastAPIInstrumentor = None  # type: ignore
    SQLAlchemyInstrumentor = None  # type: ignore
    MeterProvider = None  # type: ignore
    PeriodicExportingMetricReader = None  # type: ignore
    Resource = None  # type: ignore
    TracerProvider = None  # type: ignore
    BatchSpanProcessor = None  # type: ignore
    OTLPMetricExporter = None  # type: ignore
    OTLPSpanExporter = None  # type: ignore

from monitoring.config import Settings

logger = logging.getLogger("monitoring")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", current_trace_id()),
            "correlation_id": getattr(record, "correlation_id", None),
            "event_id": getattr(record, "event_id", None),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())


def configure_otel(settings: Settings, app: Any | None = None, engine: Any | None = None) -> None:
    if not settings.otel_enabled or trace is None or metrics is None:
        return
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "deployment.environment": settings.deployment_environment,
        }
    )
    tracer_provider = TracerProvider(resource=resource)
    if settings.otel_exporter_otlp_endpoint:
        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)))
    trace.set_tracer_provider(tracer_provider)

    if settings.otel_exporter_otlp_endpoint:
        metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint))
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    if app is not None and FastAPIInstrumentor is not None:
        FastAPIInstrumentor.instrument_app(app)
    if engine is not None and SQLAlchemyInstrumentor is not None:
        SQLAlchemyInstrumentor().instrument(engine=engine)


def current_trace_id() -> str | None:
    if trace is None:
        return None
    span = trace.get_current_span()
    context = span.get_span_context()
    if not context or not context.trace_id:
        return None
    return f"{context.trace_id:032x}"


def get_meter_counter(name: str, description: str) -> Callable[[int, dict[str, Any] | None], None]:
    if metrics is None:
        return lambda amount, attrs=None: None
    counter = metrics.get_meter("monitoring-service").create_counter(name, description=description)
    return lambda amount, attrs=None: counter.add(amount, attrs or {})


async def request_logging_middleware(request: Request, call_next: Callable[[Request], Any]) -> Any:
    start = time.perf_counter()
    correlation_id = request.headers.get("x-correlation-id") or str(uuid4())
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "http_request_completed",
        extra={
            "trace_id": current_trace_id(),
            "correlation_id": correlation_id,
            "event_id": None,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": elapsed_ms,
        },
    )
    response.headers["x-correlation-id"] = correlation_id
    return response


def log_event(message: str, *, event_id: str | None = None, correlation_id: str | None = None, **extra: Any) -> None:
    logger.info(
        message,
        extra={"trace_id": current_trace_id(), "event_id": event_id, "correlation_id": correlation_id, **extra},
    )
