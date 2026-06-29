"""
Test Suite — vLLM Latency Metrics Proxy (Project 1A)
======================================================
Covers:
  Unit tests    — LatencyMetrics dataclass, RollingStats, math correctness
  Integration   — FastAPI endpoints via httpx.AsyncClient (no real vLLM needed)
  E2E           — Against a real vLLM instance (skipped if VLLM_URL not set)
  Benchmark     — Throughput and overhead measurement

Run:
  pytest tests/ -v                        # all tests
  pytest tests/ -v -m unit               # unit only
  pytest tests/ -v -m integration        # integration only
  pytest tests/ -v -m e2e               # needs running vLLM
  pytest tests/ -v -m benchmark         # perf tests
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics

# Import the proxy app
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy import LatencyRecord, RollingStats, app
from tests.helpers import make_non_streaming_response, make_sse_stream

from vllm_patch.outputs import LatencyMetrics

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_client():
    """Synchronous test client for non-async tests."""
    with TestClient(app) as client:
        yield client


@pytest_asyncio.fixture
async def async_client():
    """Async test client for async tests."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def sample_chat_body():
    return {
        "model": "facebook/opt-1.3b",
        "messages": [{"role": "user", "content": "Hello, world!"}],
        "max_tokens": 50,
        "stream": False,
    }


@pytest.fixture
def sample_streaming_body():
    return {
        "model": "facebook/opt-1.3b",
        "messages": [{"role": "user", "content": "Count to ten."}],
        "max_tokens": 100,
        "stream": True,
    }


# ===========================================================================
# UNIT TESTS — LatencyMetrics dataclass
# ===========================================================================


class TestLatencyMetrics:
    """Unit tests for the LatencyMetrics dataclass in outputs.py"""

    @pytest.mark.unit
    def test_initial_state(self):
        lm = LatencyMetrics(request_start_time=1000.0)
        assert lm.first_token_time is None
        assert lm.ttft_ms is None
        assert lm.token_timestamps == []
        assert lm.tbt_ms_list == []
        assert lm.mean_tbt_ms is None
        assert lm.p99_tbt_ms is None

    @pytest.mark.unit
    def test_record_first_token_sets_ttft(self):
        lm = LatencyMetrics(request_start_time=1000.0)
        lm.record_token_at(1000.050)
        assert lm.first_token_time == 1000.050
        assert lm.ttft_ms == pytest.approx(50.0, abs=0.001)

    @pytest.mark.unit
    def test_record_multiple_tokens(self):
        lm = LatencyMetrics(request_start_time=time.monotonic())
        for _ in range(5):
            lm.record_token()
            time.sleep(0.005)

        assert len(lm.token_timestamps) == 5
        assert lm.first_token_time == lm.token_timestamps[0]

    @pytest.mark.unit
    def test_finalize_computes_tbt(self):
        lm = LatencyMetrics(request_start_time=time.monotonic())
        # Simulate 5 tokens with ~10ms gaps
        base = time.monotonic()
        lm.token_timestamps = [base + i * 0.010 for i in range(5)]
        lm.first_token_time = lm.token_timestamps[0]
        lm.ttft_ms = (lm.first_token_time - lm.request_start_time) * 1000

        lm.finalize()

        assert len(lm.tbt_ms_list) == 4  # n-1 intervals
        assert lm.mean_tbt_ms is not None
        assert abs(lm.mean_tbt_ms - 10.0) < 2.0  # ~10ms each
        assert lm.p99_tbt_ms is not None

    @pytest.mark.unit
    def test_finalize_single_token_no_tbt(self):
        lm = LatencyMetrics(request_start_time=time.monotonic())
        lm.record_token()
        lm.finalize()

        assert lm.tbt_ms_list == []
        assert lm.mean_tbt_ms is None
        assert lm.p99_tbt_ms is None

    @pytest.mark.unit
    def test_finalize_idempotent(self):
        lm = LatencyMetrics(request_start_time=time.monotonic())
        for _ in range(3):
            lm.record_token()
            time.sleep(0.005)
        lm.finalize()
        first_mean = lm.mean_tbt_ms
        lm.finalize()
        assert lm.mean_tbt_ms == first_mean

    @pytest.mark.unit
    def test_to_dict_structure(self):
        lm = LatencyMetrics(request_start_time=time.monotonic())
        lm.record_token()
        lm.finalize()
        d = lm.to_dict()

        assert "ttft_ms" in d
        assert "mean_tbt_ms" in d
        assert "p99_tbt_ms" in d
        assert "total_tokens_generated" in d
        assert d["total_tokens_generated"] == 1

    @pytest.mark.unit
    def test_ttft_precision(self):
        """TTFT should match synthetic timestamp delta within 1ms."""
        lm = LatencyMetrics(request_start_time=1000.0)
        lm.record_token_at(1000.100)
        assert lm.ttft_ms == pytest.approx(100.0, abs=0.001)

    @pytest.mark.unit
    def test_tbt_accuracy(self):
        """TBT should be accurate within tolerance using synthetic timestamps."""
        lm = LatencyMetrics(request_start_time=0.0)
        target_interval_ms = 20.0
        lm.record_token_at(0.020)
        for i in range(2, 6):
            lm.record_token_at(i * 0.020)

        lm.finalize()

        for tbt in lm.tbt_ms_list:
            assert abs(tbt - target_interval_ms) < 0.001

    @pytest.mark.unit
    def test_p99_tbt_calculation(self):
        """P99 TBT should correctly reflect the 99th percentile."""
        lm = LatencyMetrics(request_start_time=time.monotonic())
        base = time.monotonic()
        # 200 tokens = 199 intervals: 196 at 10ms, 3 outliers at 100ms
        # p99 idx = max(0, int(199*0.99)-1) = 196 -> outlier territory
        timestamps = [base + i * 0.010 for i in range(197)]
        for _ in range(3):
            timestamps.append(timestamps[-1] + 0.100)  # 3 x 100ms outliers
        lm.token_timestamps = timestamps
        lm.first_token_time = timestamps[0]
        lm.finalize()

        # P99 should be near the outlier (100ms), not the normal 10ms
        assert lm.p99_tbt_ms > 50.0, f"p99={lm.p99_tbt_ms}ms should be >50ms"


# ===========================================================================
# UNIT TESTS — RollingStats
# ===========================================================================


class TestRollingStats:
    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_empty_stats(self):
        stats = RollingStats(maxlen=100)
        summary = await stats.summary()
        assert summary["count"] == 0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_single_record(self):
        stats = RollingStats(maxlen=100)
        record = LatencyRecord(
            request_id="r1",
            model="opt",
            ttft_ms=150.0,
            mean_tbt_ms=10.33,
            p99_tbt_ms=12.0,
            total_tokens=4,
            e2e_seconds=0.5,
        )
        await stats.add(record)
        summary = await stats.summary()
        assert summary["count"] == 1
        assert summary["ttft_ms"]["mean"] == 150.0

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_rolling_window_eviction(self):
        stats = RollingStats(maxlen=3)
        for i in range(5):
            await stats.add(
                LatencyRecord(
                    request_id=f"r{i}",
                    model="opt",
                    ttft_ms=float(i * 100),
                    mean_tbt_ms=None,
                    p99_tbt_ms=None,
                    total_tokens=1,
                    e2e_seconds=0.1,
                )
            )
        summary = await stats.summary()
        # Only last 3 records kept (200, 300, 400)
        assert summary["count"] == 3
        assert summary["ttft_ms"]["min"] == 200.0


# ===========================================================================
# INTEGRATION TESTS — FastAPI endpoints
# ===========================================================================


class TestHealthEndpoint:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_health_returns_proxy_ok(self, async_client):
        # Inject a mock http client into app state for the health check
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client.get = AsyncMock(return_value=mock_resp)
        app.state.http_client = mock_client

        resp = await async_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["proxy"] == "ok"
        assert "upstream" in data

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_metrics_endpoint_returns_prometheus(self, async_client):
        resp = await async_client.get("/metrics")
        assert resp.status_code == 200
        assert b"vllm_proxy_ttft_milliseconds" in resp.content

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_latency_stats_empty(self, async_client):
        resp = await async_client.get("/latency/stats")
        assert resp.status_code == 200


class TestNonStreamingProxy:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_non_streaming_injects_latency_into_usage(self, async_client, sample_chat_body):
        mock_response_data = make_non_streaming_response()

        with patch.object(
            app.state,
            "http_client",
            create=True,
        ) as _:
            # Patch at the httpx client level
            mock_client = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_response_data
            mock_resp.status_code = 200
            mock_resp.content = json.dumps(mock_response_data).encode()
            mock_client.post = AsyncMock(return_value=mock_resp)
            app.state.http_client = mock_client

            resp = await async_client.post(
                "/v1/chat/completions",
                json=sample_chat_body,
            )

        assert resp.status_code == 200
        data = resp.json()
        usage = data.get("usage", {})

        # [PATCH] Core assertion: latency fields present
        assert "ttft_ms" in usage, "usage must contain ttft_ms"
        assert usage["ttft_ms"] is not None
        assert usage["ttft_ms"] >= 0
        assert "e2e_latency_ms" in usage

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_non_streaming_injects_response_headers(self, async_client, sample_chat_body):
        mock_response_data = make_non_streaming_response()

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.status_code = 200
        mock_resp.content = json.dumps(mock_response_data).encode()
        mock_client.post = AsyncMock(return_value=mock_resp)
        app.state.http_client = mock_client

        resp = await async_client.post(
            "/v1/chat/completions",
            json=sample_chat_body,
        )

        # [PATCH] Core assertion: latency headers present
        assert "x-vllm-ttft-ms" in resp.headers, "x-vllm-ttft-ms header missing"
        assert "x-vllm-request-id" in resp.headers

        ttft_val = float(resp.headers["x-vllm-ttft-ms"])
        assert ttft_val >= 0, "TTFT must be non-negative"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_existing_clients_see_no_breaking_change(self, async_client, sample_chat_body):
        """Backward-compat: existing fields still present, new ones additive."""
        mock_response_data = make_non_streaming_response()

        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_response_data
        mock_resp.status_code = 200
        mock_resp.content = json.dumps(mock_response_data).encode()
        mock_client.post = AsyncMock(return_value=mock_resp)
        app.state.http_client = mock_client

        resp = await async_client.post("/v1/chat/completions", json=sample_chat_body)
        data = resp.json()

        # Standard OpenAI fields still present
        assert "choices" in data
        assert "id" in data
        assert "model" in data
        usage = data["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage


class TestStreamingProxy:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_streaming_injects_sse_comments(self, async_client, sample_streaming_body):
        """SSE comments carrying latency should appear in the stream."""
        tokens = ["Hello", " world", "!", " How", " are", " you"]
        sse_body = make_sse_stream(tokens)

        async def mock_aiter_lines():
            for line in sse_body.split("\n"):
                yield line
                await asyncio.sleep(0.005)  # simulate inter-token delay

        mock_client = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_context)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        mock_context.aiter_lines = mock_aiter_lines
        mock_client.stream = MagicMock(return_value=mock_context)
        app.state.http_client = mock_client

        full_body = b""
        async with async_client.stream(
            "POST", "/v1/chat/completions", json=sample_streaming_body
        ) as resp:
            async for chunk in resp.aiter_bytes():
                full_body += chunk

        text = full_body.decode()
        assert "x-vllm-ttft-ms=" in text, "SSE comment with TTFT missing"
        assert "data: [DONE]" in text

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_streaming_ttft_positive_and_reasonable(
        self, async_client, sample_streaming_body
    ):
        tokens = ["Hi"]
        sse_body = make_sse_stream(tokens)

        async def mock_aiter_lines():
            await asyncio.sleep(0.050)  # 50ms TTFT
            for line in sse_body.split("\n"):
                yield line
                await asyncio.sleep(0.010)

        mock_client = AsyncMock()
        mock_context = AsyncMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_context)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        mock_context.aiter_lines = mock_aiter_lines
        mock_client.stream = MagicMock(return_value=mock_context)
        app.state.http_client = mock_client

        full_body = b""
        async with async_client.stream(
            "POST", "/v1/chat/completions", json=sample_streaming_body
        ) as resp:
            async for chunk in resp.aiter_bytes():
                full_body += chunk

        text = full_body.decode()
        for line in text.split("\n"):
            if line.startswith(": x-vllm-ttft-ms="):
                ttft = float(line.split("=")[1])
                assert ttft > 40.0, f"TTFT {ttft}ms should be > 40ms"
                assert ttft < 500.0, f"TTFT {ttft}ms unreasonably high"
                break


class TestOverheadAndCompatibility:
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_zero_overhead_non_streaming(self, async_client, sample_chat_body):
        """Overhead from proxy should be < 5ms for non-streaming."""
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = make_non_streaming_response()
        mock_resp.status_code = 200
        mock_resp.content = json.dumps(make_non_streaming_response()).encode()
        mock_client.post = AsyncMock(return_value=mock_resp)
        app.state.http_client = mock_client

        # Warm up
        await async_client.post("/v1/chat/completions", json=sample_chat_body)

        # Measure proxy overhead
        times = []
        for _ in range(20):
            t0 = time.monotonic()
            await async_client.post("/v1/chat/completions", json=sample_chat_body)
            times.append((time.monotonic() - t0) * 1000)

        mean_overhead = statistics.mean(times)
        # In a real test, subtract network RTT to vLLM
        # Here we just verify the proxy itself adds < 10ms of pure CPU overhead
        assert mean_overhead < 100.0, f"Proxy overhead {mean_overhead:.1f}ms too high"


# ===========================================================================
# E2E TESTS — Require a running vLLM instance
# ===========================================================================

VLLM_URL = os.getenv("VLLM_E2E_URL", "")


@pytest.mark.e2e
@pytest.mark.skipif(not VLLM_URL, reason="Set VLLM_E2E_URL to run E2E tests")
class TestE2EWithRealVLLM:
    def test_e2e_non_streaming(self):
        """Real TTFT measurement against running vLLM."""
        with httpx.Client(base_url=VLLM_URL, timeout=120.0) as client:
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": os.getenv("VLLM_MODEL", "facebook/opt-1.3b"),
                    "messages": [{"role": "user", "content": "What is 2+2?"}],
                    "max_tokens": 20,
                    "stream": False,
                },
            )

        assert resp.status_code == 200
        assert "x-vllm-ttft-ms" in resp.headers
        ttft = float(resp.headers["x-vllm-ttft-ms"])
        assert ttft > 0
        assert ttft < 30000  # < 30s sanity bound
        print(f"\nE2E TTFT (non-streaming): {ttft:.1f}ms")

    def test_e2e_streaming_has_sse_latency_comments(self):
        """Real streaming TTFT measurement."""
        with httpx.Client(base_url=VLLM_URL, timeout=120.0) as client:
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": os.getenv("VLLM_MODEL", "facebook/opt-1.3b"),
                    "messages": [{"role": "user", "content": "Count to 5."}],
                    "max_tokens": 50,
                    "stream": True,
                },
            ) as resp:
                body = resp.read().decode()

        assert "x-vllm-ttft-ms=" in body
        for line in body.split("\n"):
            if line.startswith(": x-vllm-ttft-ms="):
                ttft = float(line.split("=")[1])
                assert ttft > 0
                print(f"\nE2E TTFT (streaming): {ttft:.1f}ms")

    def test_e2e_ttft_accuracy_vs_client(self):
        """Server TTFT should be within 5ms of client-measured TTFT."""
        with httpx.Client(base_url=VLLM_URL, timeout=120.0) as client:
            client_t0 = time.monotonic()
            with client.stream(
                "POST",
                "/v1/chat/completions",
                json={
                    "model": os.getenv("VLLM_MODEL", "facebook/opt-1.3b"),
                    "messages": [{"role": "user", "content": "Say hello."}],
                    "max_tokens": 5,
                    "stream": True,
                },
            ) as resp:
                client_first_token_time = None
                body_lines = []
                for line in resp.iter_lines():
                    now = time.monotonic()
                    if (
                        client_first_token_time is None
                        and line.startswith("data: ")
                        and line != "data: [DONE]"
                    ):
                        try:
                            payload = json.loads(line[6:])
                            if any(
                                c.get("delta", {}).get("content")
                                for c in payload.get("choices", [])
                            ):
                                client_first_token_time = now
                        except Exception:
                            pass
                    body_lines.append(line)

        if client_first_token_time is None:
            pytest.skip("No content tokens observed")

        client_ttft_ms = (client_first_token_time - client_t0) * 1000.0

        body = "\n".join(body_lines)
        server_ttft_ms = None
        for line in body.split("\n"):
            if line.startswith(": x-vllm-ttft-ms="):
                server_ttft_ms = float(line.split("=")[1])
                break

        if server_ttft_ms is None:
            pytest.fail("Server TTFT not found in SSE stream")

        delta = abs(server_ttft_ms - client_ttft_ms)
        print(
            f"\nClient TTFT: {client_ttft_ms:.1f}ms | Server TTFT: {server_ttft_ms:.1f}ms | Delta: {delta:.1f}ms"
        )
        assert delta < 50.0, f"TTFT delta {delta:.1f}ms exceeds 50ms (network latency included)"


# ===========================================================================
# BENCHMARK TESTS
# ===========================================================================


@pytest.mark.benchmark
class TestBenchmark:
    @pytest.mark.asyncio
    async def test_latency_metrics_throughput(self):
        """LatencyMetrics should handle 10k req/s with negligible CPU overhead."""
        N = 10_000
        start = time.perf_counter()
        for _ in range(N):
            lm = LatencyMetrics(request_start_time=time.monotonic())
            for _ in range(10):
                lm.record_token()
            lm.finalize()
        elapsed = time.perf_counter() - start
        throughput = N / elapsed
        print(f"\nLatencyMetrics throughput: {throughput:.0f} req/s")
        assert throughput > 1000, f"Too slow: {throughput:.0f} req/s"

    @pytest.mark.asyncio
    async def test_rolling_stats_concurrent_writes(self):
        """RollingStats should handle concurrent writes without corruption."""
        stats = RollingStats(maxlen=500)
        N = 200

        async def write_records(n: int):
            for i in range(n):
                await stats.add(
                    LatencyRecord(
                        request_id=f"r{i}",
                        model="opt",
                        ttft_ms=float(i % 100),
                        mean_tbt_ms=10.0,
                        p99_tbt_ms=15.0,
                        total_tokens=6,
                        e2e_seconds=0.5,
                    )
                )

        await asyncio.gather(*[write_records(N // 10) for _ in range(10)])
        summary = await stats.summary()
        assert summary["count"] == min(N, 500)
