"""
vLLM Latency Metrics Proxy — Project 1A
========================================
Transparent FastAPI proxy that measures TTFT/TBT and surfaces them via
response headers, usage fields, SSE comments, and Prometheus metrics.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from vllm_patch import telemetry
from vllm_patch.latency_utils import (
    LatencySnapshot,
    StreamLatencyTracker,
    body_requests_stream,
    percentile,
    sse_chunk_has_content,
    sse_line_may_have_content,
)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VLLM_BASE_URL = os.getenv("VLLM_BASE_URL", "http://localhost:8000")
PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")
PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
STATS_WINDOW = int(os.getenv("STATS_WINDOW", "1000"))
MAX_TBT_PROMETHEUS_SAMPLES = int(os.getenv("MAX_TBT_PROMETHEUS_SAMPLES", "256"))

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("vllm-latency-proxy")

# ---------------------------------------------------------------------------
# Prometheus Metrics
# ---------------------------------------------------------------------------

TTFT_HISTOGRAM = Histogram(
    "vllm_proxy_ttft_milliseconds",
    "Time-To-First-Token measured by proxy (ms)",
    buckets=[10, 25, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
)
TBT_HISTOGRAM = Histogram(
    "vllm_proxy_tbt_milliseconds",
    "Time-Between-Tokens measured by proxy (ms)",
    buckets=[1, 5, 10, 25, 50, 100, 200, 500, 1000],
)
REQUEST_COUNTER = Counter(
    "vllm_proxy_requests_total",
    "Total requests proxied",
    ["endpoint", "status"],
)
ACTIVE_REQUESTS = Gauge(
    "vllm_proxy_active_requests",
    "Currently in-flight requests",
)
E2E_LATENCY = Histogram(
    "vllm_proxy_e2e_latency_seconds",
    "End-to-end request latency in seconds",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120],
)

# ---------------------------------------------------------------------------
# Rolling stats — compact records, single lock, no fire-and-forget tasks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class LatencyRecord:
    request_id: str
    model: str
    ttft_ms: float
    mean_tbt_ms: float | None
    p99_tbt_ms: float | None
    total_tokens: int
    e2e_seconds: float
    timestamp: float = field(default_factory=time.time)


class RollingStats:
    """Rolling window with sync append (hot path) and async summary (API)."""

    def __init__(self, maxlen: int = STATS_WINDOW) -> None:
        self._records: deque[LatencyRecord] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def add_sync(self, record: LatencyRecord) -> None:
        with self._lock:
            self._records.append(record)

    async def add(self, record: LatencyRecord) -> None:
        self.add_sync(record)

    async def summary(self) -> dict:
        with self._lock:
            if not self._records:
                return {"count": 0}
            records = list(self._records)

        ttfts = [r.ttft_ms for r in records]
        mean_tbts = [r.mean_tbt_ms for r in records if r.mean_tbt_ms is not None]

        import statistics

        return {
            "count": len(records),
            "window_size": STATS_WINDOW,
            "ttft_ms": {
                "p50": round(percentile(ttfts, 0.50) or 0.0, 2),
                "p95": round(percentile(ttfts, 0.95) or 0.0, 2),
                "p99": round(percentile(ttfts, 0.99) or 0.0, 2),
                "mean": round(statistics.mean(ttfts), 2),
                "min": round(min(ttfts), 2),
                "max": round(max(ttfts), 2),
            },
            "mean_tbt_ms": {
                "p50": round(percentile(mean_tbts, 0.50), 2) if mean_tbts else None,
                "p95": round(percentile(mean_tbts, 0.95), 2) if mean_tbts else None,
                "p99": round(percentile(mean_tbts, 0.99), 2) if mean_tbts else None,
                "mean": round(statistics.mean(mean_tbts), 2) if mean_tbts else None,
            },
        }


STATS = RollingStats()

# ---------------------------------------------------------------------------
# Metrics recording helpers
# ---------------------------------------------------------------------------


def _record_prometheus(
    snapshot: LatencySnapshot,
    tbt_samples: tuple[float, ...] = (),
) -> None:
    TTFT_HISTOGRAM.observe(snapshot.ttft_ms)
    E2E_LATENCY.observe(snapshot.e2e_ms / 1000.0)
    samples = tbt_samples or snapshot.tbt_ms_list
    if len(samples) > MAX_TBT_PROMETHEUS_SAMPLES:
        step = max(1, len(samples) // MAX_TBT_PROMETHEUS_SAMPLES)
        samples = samples[::step]
    for tbt in samples:
        TBT_HISTOGRAM.observe(tbt)


def _record_to_stats_sync(
    snapshot: LatencySnapshot,
    request_id: str,
    model: str,
) -> None:
    STATS.add_sync(
        LatencyRecord(
            request_id=request_id,
            model=model,
            ttft_ms=snapshot.ttft_ms,
            mean_tbt_ms=snapshot.mean_tbt_ms,
            p99_tbt_ms=snapshot.p99_tbt_ms,
            total_tokens=snapshot.total_tokens,
            e2e_seconds=snapshot.e2e_ms / 1000.0,
        )
    )


async def _record_to_stats(
    snapshot: LatencySnapshot,
    request_id: str,
    model: str,
) -> None:
    _record_to_stats_sync(snapshot, request_id, model)


def _parse_sse_content_line(line: str) -> bool:
    """Return True if SSE data line carries generative content."""
    if not sse_line_may_have_content(line):
        return False
    try:
        return sse_chunk_has_content(json.loads(line[6:]))
    except json.JSONDecodeError:
        return False


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("vLLM Latency Proxy starting → upstream: %s", VLLM_BASE_URL)
    app.state.http_client = httpx.AsyncClient(
        base_url=VLLM_BASE_URL,
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0),
        limits=httpx.Limits(max_connections=500, max_keepalive_connections=100),
    )
    yield
    await app.state.http_client.aclose()
    log.info("vLLM Latency Proxy shut down")


app = FastAPI(
    title="vLLM Latency Metrics Proxy",
    description="Transparent proxy that measures and surfaces TTFT/TBT metrics for vLLM",
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "x-vllm-ttft-ms",
        "x-vllm-mean-tbt-ms",
        "x-vllm-p99-tbt-ms",
        "x-vllm-tokens-generated",
        "x-vllm-e2e-latency-ms",
        "x-vllm-request-id",
        "traceparent",
        "x-trace-id",
        "x-span-id",
    ],
)

telemetry.init_tracing(app)


@app.get("/health")
async def health() -> dict:
    client: httpx.AsyncClient = app.state.http_client
    try:
        resp = await client.get("/health", timeout=3.0)
        upstream_ok = resp.status_code == 200
    except Exception:
        upstream_ok = False
    return {
        "proxy": "ok",
        "upstream": "ok" if upstream_ok else "unreachable",
        "upstream_url": VLLM_BASE_URL,
    }


@app.get("/metrics")
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/latency/stats")
async def latency_stats() -> dict:
    return await STATS.summary()


@app.api_route("/v1/chat/completions", methods=["POST"])
async def proxy_chat_completions(request: Request) -> Response:
    return await _proxy_request(request, "/v1/chat/completions")


@app.api_route("/v1/completions", methods=["POST"])
async def proxy_completions(request: Request) -> Response:
    return await _proxy_request(request, "/v1/completions")


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy_passthrough(request: Request, path: str) -> Response:
    client: httpx.AsyncClient = app.state.http_client
    body = await request.body()
    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")
    }
    try:
        resp = await client.request(
            method=request.method,
            url=f"/{path}",
            headers=headers,
            content=body,
            params=dict(request.query_params),
        )
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )
    except Exception as exc:
        log.error("Passthrough error for /%s: %s", path, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


def _trace_headers() -> dict[str, str]:
    if not telemetry.is_enabled():
        return {}
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context()
        if ctx and ctx.is_valid:
            return {
                "x-trace-id": format(ctx.trace_id, "032x"),
                "x-span-id": format(ctx.span_id, "016x"),
            }
    except Exception:
        pass
    return {}


async def _proxy_request(request: Request, path: str) -> Response:
    client: httpx.AsyncClient = app.state.http_client
    request_start = time.monotonic()

    body_bytes = await request.body()
    is_streaming = body_requests_stream(body_bytes)
    if is_streaming:
        body_json = {}
    else:
        try:
            body_json = json.loads(body_bytes)
        except json.JSONDecodeError:
            body_json = {}
        is_streaming = body_json.get("stream", False)
    model = body_json.get("model", "unknown")
    request_id = request.headers.get("x-request-id", f"req-{uuid.uuid4().hex}")

    headers = {
        k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")
    }
    headers["x-request-id"] = request_id

    ACTIVE_REQUESTS.inc()
    REQUEST_COUNTER.labels(endpoint=path, status="started").inc()

    try:
        with telemetry.request_span(request_id, path, model, is_streaming) as span:
            if is_streaming:
                return await _handle_streaming(
                    client,
                    path,
                    headers,
                    body_bytes,
                    request_id,
                    model,
                    request_start,
                    span,
                )
            return await _handle_non_streaming(
                client,
                path,
                headers,
                body_bytes,
                request_id,
                model,
                request_start,
                span,
            )
    except Exception as exc:
        REQUEST_COUNTER.labels(endpoint=path, status="error").inc()
        ACTIVE_REQUESTS.dec()
        log.error("Proxy error [%s]: %s", request_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)


async def _handle_streaming(
    client: httpx.AsyncClient,
    path: str,
    headers: dict,
    body_bytes: bytes,
    request_id: str,
    model: str,
    request_start: float,
    span: object,
) -> StreamingResponse:
    async def generate():
        tracker = StreamLatencyTracker(request_start)
        first_token_recorded = False
        status_code = 200
        try:
            with telemetry.upstream_span(request_id, path):
                async with client.stream(
                    "POST", path, headers=headers, content=body_bytes
                ) as upstream:
                    status_code = upstream.status_code
                    async for line in upstream.aiter_lines():
                        if not line:
                            yield b"\n"
                            continue

                        if line.startswith("data: ") and line != "data: [DONE]":
                            if _parse_sse_content_line(line):
                                now = time.monotonic()
                                tracker.on_content_token(now)
                                if not first_token_recorded:
                                    first_token_recorded = True
                                    ttft_ms = (now - request_start) * 1000.0
                                    telemetry.record_first_token(span, ttft_ms)

                        if line == "data: [DONE]":
                            now = time.monotonic()
                            snapshot = tracker.finalize(now)
                            tbt_samples = tracker.prometheus_tbt_samples()
                            telemetry.record_completion(
                                span,
                                ttft_ms=snapshot.ttft_ms,
                                mean_tbt_ms=snapshot.mean_tbt_ms,
                                p99_tbt_ms=snapshot.p99_tbt_ms,
                                total_tokens=snapshot.total_tokens,
                                e2e_ms=snapshot.e2e_ms,
                            )
                            yield (line + "\n").encode()
                            for comment in snapshot.to_sse_comment_lines():
                                yield f"{comment}\n".encode()
                            _record_prometheus(snapshot, tbt_samples)
                            _record_to_stats_sync(snapshot, request_id, model)
                            REQUEST_COUNTER.labels(endpoint=path, status=str(status_code)).inc()
                            continue

                        yield (line + "\n").encode()
        finally:
            ACTIVE_REQUESTS.dec()

    response_headers = {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "x-vllm-request-id": request_id,
        **_trace_headers(),
    }

    return StreamingResponse(
        generate(),
        headers=response_headers,
        media_type="text/event-stream",
    )


async def _handle_non_streaming(
    client: httpx.AsyncClient,
    path: str,
    headers: dict,
    body_bytes: bytes,
    request_id: str,
    model: str,
    request_start: float,
    span: object,
) -> Response:
    try:
        with telemetry.upstream_span(request_id, path):
            resp = await client.post(path, headers=headers, content=body_bytes)
    finally:
        ACTIVE_REQUESTS.dec()

    e2e_ms = (time.monotonic() - request_start) * 1000.0

    try:
        data = resp.json()
    except Exception:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=dict(resp.headers),
        )

    usage = data.get("usage", {})
    completion_tokens = usage.get("completion_tokens", 0)
    snapshot = LatencySnapshot(
        ttft_ms=e2e_ms,
        mean_tbt_ms=None,
        p99_tbt_ms=None,
        total_tokens=completion_tokens,
        e2e_ms=e2e_ms,
    )
    telemetry.record_first_token(span, e2e_ms)
    telemetry.record_completion(
        span,
        ttft_ms=e2e_ms,
        mean_tbt_ms=None,
        p99_tbt_ms=None,
        total_tokens=completion_tokens,
        e2e_ms=e2e_ms,
    )
    usage.update(snapshot.to_usage_fields())
    usage["mean_tbt_ms"] = None
    usage["p99_tbt_ms"] = None
    data["usage"] = usage

    _record_prometheus(snapshot)
    await _record_to_stats(snapshot, request_id, model)
    REQUEST_COUNTER.labels(endpoint=path, status=str(resp.status_code)).inc()

    response_headers = snapshot.to_response_headers(request_id)
    response_headers["Content-Type"] = "application/json"
    response_headers.update(_trace_headers())

    return Response(
        content=json.dumps(data),
        status_code=resp.status_code,
        headers=response_headers,
        media_type="application/json",
    )


if __name__ == "__main__":
    import sys

    import uvicorn

    loop = "uvloop" if sys.platform != "win32" else "asyncio"
    uvicorn.run(
        "proxy:app",
        host=PROXY_HOST,
        port=PROXY_PORT,
        log_level=LOG_LEVEL.lower(),
        access_log=True,
        loop=loop,
    )
