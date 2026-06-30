"""Tests for optional OpenTelemetry instrumentation."""

import os

import pytest


@pytest.mark.unit
def test_telemetry_disabled_by_default():
    os.environ.pop("OTEL_ENABLED", None)
    os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
    from vllm_patch import telemetry

    telemetry._initialized = False
    telemetry._tracer = None
    telemetry.init_tracing()
    assert telemetry.get_tracer() is not None
    with telemetry.request_span("req-1", "/v1/chat/completions", "test-model", True):
        pass


@pytest.mark.unit
def test_record_completion_noop():
    from vllm_patch.telemetry import _NoopSpan, record_completion

    span = _NoopSpan()
    record_completion(
        span,
        ttft_ms=100.0,
        mean_tbt_ms=20.0,
        p99_tbt_ms=50.0,
        total_tokens=10,
        e2e_ms=500.0,
    )
