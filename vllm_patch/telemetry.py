"""
OpenTelemetry instrumentation for the vLLM latency proxy.

Enabled when OTEL_ENABLED=true or OTEL_EXPORTER_OTLP_ENDPOINT is set.
Exports traces via OTLP (gRPC or HTTP) to Jaeger, Grafana Tempo, or any
OpenTelemetry Collector.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

log = logging.getLogger("vllm-latency-proxy.telemetry")

OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in ("1", "true", "yes")
OTEL_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "vllm-latency-proxy")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
OTEL_EXPORTER_OTLP_PROTOCOL = os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")

_tracer: Any = None
_initialized = False


class _NoopSpan:
    def set_attribute(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def add_event(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def record_exception(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def set_status(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def end(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> _NoopSpan:
        return self

    def __exit__(self, *_args: Any) -> None:
        pass


class _NoopTracer:
    def start_span(self, *_args: Any, **_kwargs: Any) -> _NoopSpan:
        return _NoopSpan()

    @contextmanager
    def start_as_current_span(
        self, *_args: Any, **_kwargs: Any
    ) -> Generator[_NoopSpan, None, None]:
        yield _NoopSpan()


def is_enabled() -> bool:
    return OTEL_ENABLED or bool(OTEL_EXPORTER_OTLP_ENDPOINT)


def init_tracing(app: Any = None) -> None:
    """Initialize OTLP trace export. Safe to call multiple times."""
    global _tracer, _initialized
    if _initialized:
        return
    _initialized = True

    if not is_enabled():
        log.info("OpenTelemetry disabled (set OTEL_ENABLED=true to enable)")
        _tracer = _NoopTracer()
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GRPCExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
        from opentelemetry.semconv.resource import ResourceAttributes
    except ImportError:
        log.warning(
            "OpenTelemetry packages not installed; tracing disabled. "
            "Install opentelemetry-* packages from requirements.txt."
        )
        _tracer = _NoopTracer()
        return

    resource = Resource.create(
        {
            ResourceAttributes.SERVICE_NAME: OTEL_SERVICE_NAME,
            ResourceAttributes.SERVICE_VERSION: os.getenv("OTEL_SERVICE_VERSION", "1.2.0"),
        }
    )
    provider = TracerProvider(resource=resource)
    endpoint = OTEL_EXPORTER_OTLP_ENDPOINT or "http://localhost:4317"

    exporter: SpanExporter
    if OTEL_EXPORTER_OTLP_PROTOCOL.lower() == "http/protobuf":
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPExporter,
        )

        exporter = HTTPExporter(endpoint=endpoint)
    else:
        exporter = GRPCExporter(endpoint=endpoint, insecure=True)

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer(OTEL_SERVICE_NAME)

    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
            from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

            FastAPIInstrumentor.instrument_app(app)
            HTTPXClientInstrumentor().instrument()
        except ImportError:
            log.warning("FastAPI/HTTPX OTel instrumentation packages not installed")

    log.info(
        "OpenTelemetry enabled: service=%s endpoint=%s protocol=%s",
        OTEL_SERVICE_NAME,
        endpoint,
        OTEL_EXPORTER_OTLP_PROTOCOL,
    )


def get_tracer() -> Any:
    if not _initialized:
        init_tracing()
    return _tracer or _NoopTracer()


@contextmanager
def request_span(
    request_id: str,
    endpoint: str,
    model: str,
    streaming: bool,
) -> Generator[Any, None, None]:
    """Top-level span for an inference request."""
    tracer = get_tracer()
    with tracer.start_as_current_span("inference.request") as span:
        span.set_attribute("request.id", request_id)
        span.set_attribute("llm.endpoint", endpoint)
        span.set_attribute("llm.model", model)
        span.set_attribute("llm.streaming", streaming)
        yield span


@contextmanager
def upstream_span(request_id: str, path: str) -> Generator[Any, None, None]:
    """Span covering the proxy → vLLM HTTP call."""
    tracer = get_tracer()
    with tracer.start_as_current_span("vllm.upstream") as span:
        span.set_attribute("request.id", request_id)
        span.set_attribute("http.route", path)
        yield span


def record_first_token(span: Any, ttft_ms: float) -> None:
    span.add_event("first_token", {"ttft_ms": ttft_ms})


def record_completion(
    span: Any,
    *,
    ttft_ms: float,
    mean_tbt_ms: float | None,
    p99_tbt_ms: float | None,
    total_tokens: int,
    e2e_ms: float,
) -> None:
    span.set_attribute("latency.ttft_ms", ttft_ms)
    if mean_tbt_ms is not None:
        span.set_attribute("latency.mean_tbt_ms", mean_tbt_ms)
    if p99_tbt_ms is not None:
        span.set_attribute("latency.p99_tbt_ms", p99_tbt_ms)
    span.set_attribute("latency.total_tokens", total_tokens)
    span.set_attribute("latency.e2e_ms", e2e_ms)
    span.add_event("completion")
