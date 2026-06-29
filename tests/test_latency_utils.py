"""Unit tests for vllm_patch.latency_utils."""

from __future__ import annotations

import time

import pytest

from vllm_patch.latency_utils import (
    LatencySnapshot,
    StreamLatencyTracker,
    body_requests_stream,
    compute_tbt_ms_list,
    percentile,
    sse_chunk_has_content,
    sse_line_may_have_content,
)


class TestPercentile:
    @pytest.mark.unit
    def test_empty_returns_none(self):
        assert percentile([], 0.99) is None

    @pytest.mark.unit
    def test_single_value(self):
        assert percentile([42.0], 0.50) == 42.0

    @pytest.mark.unit
    def test_p99_indexing(self):
        values = [float(i) for i in range(100)]
        assert percentile(values, 0.99) == 98.0


class TestComputeTbt:
    @pytest.mark.unit
    def test_two_tokens(self):
        ts = [1000.0, 1000.02]
        assert compute_tbt_ms_list(ts) == pytest.approx([20.0])

    @pytest.mark.unit
    def test_single_token_empty(self):
        assert compute_tbt_ms_list([1000.0]) == []


class TestSseChunkHasContent:
    @pytest.mark.unit
    def test_delta_content(self):
        assert sse_chunk_has_content({"choices": [{"delta": {"content": "hi"}}]})

    @pytest.mark.unit
    def test_completion_text(self):
        assert sse_chunk_has_content({"choices": [{"text": "hi"}]})

    @pytest.mark.unit
    def test_tool_calls(self):
        payload = {"choices": [{"delta": {"tool_calls": [{"index": 0}]}}]}
        assert sse_chunk_has_content(payload)

    @pytest.mark.unit
    def test_empty_delta(self):
        assert not sse_chunk_has_content({"choices": [{"delta": {}}]})

    @pytest.mark.unit
    def test_role_only_skips_json(self):
        line = 'data: {"choices":[{"delta":{"role":"assistant"}}]}'
        assert not sse_line_may_have_content(line)

    @pytest.mark.unit
    def test_body_stream_fast_path(self):
        assert body_requests_stream(b'{"model":"m","stream":true}')
        assert not body_requests_stream(b'{"model":"m","stream":false}')


class TestStreamLatencyTracker:
    @pytest.mark.unit
    def test_no_tokens_uses_e2e_as_ttft(self):
        tracker = StreamLatencyTracker(1000.0)
        snap = tracker.finalize(1001.0)
        assert snap.ttft_ms == 1000.0
        assert snap.total_tokens == 0
        assert snap.mean_tbt_ms is None

    @pytest.mark.unit
    def test_deterministic_tbt(self):
        tracker = StreamLatencyTracker(0.0)
        for i in range(1, 6):
            tracker.on_content_token(float(i) * 0.020)
        snap = tracker.finalize(0.120)
        assert snap.ttft_ms == 20.0
        assert snap.mean_tbt_ms == pytest.approx(20.0)
        assert snap.total_tokens == 5

    @pytest.mark.unit
    def test_snapshot_serialisation(self):
        snap = LatencySnapshot(
            ttft_ms=100.0,
            mean_tbt_ms=25.0,
            p99_tbt_ms=50.0,
            total_tokens=10,
            e2e_ms=350.0,
            tbt_ms_list=[20.0, 30.0],
        )
        usage = snap.to_usage_fields()
        assert usage["ttft_ms"] == 100.0
        assert usage["mean_tbt_ms"] == 25.0
        headers = snap.to_response_headers("req-1")
        assert headers["x-vllm-request-id"] == "req-1"
        assert "x-vllm-mean-tbt-ms" in headers
        comments = snap.to_sse_comment_lines()
        assert any("x-vllm-ttft-ms=" in c for c in comments)
