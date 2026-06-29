"""
Shared latency measurement utilities.

Used by the deployment proxy and aligned with upstream vLLM LatencyMetrics
semantics in outputs.py.  All timestamps are time.monotonic() deltas.
"""

from __future__ import annotations

import heapq
import random
from dataclasses import dataclass, field
from typing import Any, List, Optional

# Fast substring checks before json.loads on the streaming hot path.
_SSE_CONTENT_MARKERS = (
    '"content":"',
    '"content": "',
    '"text":"',
    '"text": "',
    '"tool_calls":',
)

# Reservoir size for P99 TBT — bounds finalize work and memory at long sequences.
_TBT_RESERVOIR_SIZE = 256


def percentile(values: List[float], p: float) -> Optional[float]:
    """Return the p-th percentile (0–1) using O(n log k) selection."""
    if not values:
        return None
    n = len(values)
    idx = max(0, int(n * p) - 1)
    if n <= 32:
        return sorted(values)[idx]
    k = n - idx
    return heapq.nlargest(k, values)[-1]


def compute_tbt_ms_list(token_timestamps: List[float]) -> List[float]:
    """Inter-token intervals in milliseconds from monotonic timestamps."""
    if len(token_timestamps) < 2:
        return []
    return [
        (token_timestamps[i] - token_timestamps[i - 1]) * 1000.0
        for i in range(1, len(token_timestamps))
    ]


def body_requests_stream(body: bytes) -> bool:
    """Fast check for stream=true without parsing the full JSON body."""
    if b'"stream"' not in body and b"'stream'" not in body:
        return False
    lowered = body[:512].lower()
    return b'"stream":true' in lowered or b'"stream": true' in lowered


def sse_line_may_have_content(line: str) -> bool:
    """Cheap pre-filter before json.loads on SSE lines."""
    return any(marker in line for marker in _SSE_CONTENT_MARKERS)


def sse_chunk_has_content(payload: dict[str, Any]) -> bool:
    """True when an OpenAI SSE chunk carries generative output."""
    for choice in payload.get("choices", []):
        delta = choice.get("delta") or {}
        if delta.get("content") or choice.get("text"):
            return True
        if delta.get("tool_calls"):
            return True
    return False


@dataclass(frozen=True)
class LatencySnapshot:
    """Immutable latency result for a single request."""

    ttft_ms: float
    mean_tbt_ms: Optional[float]
    p99_tbt_ms: Optional[float]
    total_tokens: int
    e2e_ms: float
    tbt_ms_list: tuple[float, ...] = field(default_factory=tuple)

    def to_usage_fields(self) -> dict[str, Optional[float]]:
        return {
            "ttft_ms": round(self.ttft_ms, 3),
            "mean_tbt_ms": round(self.mean_tbt_ms, 3) if self.mean_tbt_ms is not None else None,
            "p99_tbt_ms": round(self.p99_tbt_ms, 3) if self.p99_tbt_ms is not None else None,
            "e2e_latency_ms": round(self.e2e_ms, 3),
        }

    def to_response_headers(self, request_id: str) -> dict[str, str]:
        headers: dict[str, str] = {
            "x-vllm-request-id": request_id,
            "x-vllm-ttft-ms": str(round(self.ttft_ms, 3)),
            "x-vllm-e2e-latency-ms": str(round(self.e2e_ms, 3)),
        }
        if self.mean_tbt_ms is not None:
            headers["x-vllm-mean-tbt-ms"] = str(round(self.mean_tbt_ms, 3))
        if self.p99_tbt_ms is not None:
            headers["x-vllm-p99-tbt-ms"] = str(round(self.p99_tbt_ms, 3))
        if self.total_tokens:
            headers["x-vllm-tokens-generated"] = str(self.total_tokens)
        return headers

    def to_sse_comment_lines(self) -> tuple[str, ...]:
        lines = [
            f": x-vllm-ttft-ms={self.ttft_ms:.3f}",
            f": x-vllm-e2e-latency-ms={self.e2e_ms:.3f}",
        ]
        if self.mean_tbt_ms is not None:
            lines.append(f": x-vllm-mean-tbt-ms={self.mean_tbt_ms:.3f}")
        if self.p99_tbt_ms is not None:
            lines.append(f": x-vllm-p99-tbt-ms={self.p99_tbt_ms:.3f}")
        if self.total_tokens:
            lines.append(f": x-vllm-tokens-generated={self.total_tokens}")
        return tuple(lines)


class StreamLatencyTracker:
    """
    Tracks TTFT/TBT while forwarding an SSE stream.

    Uses incremental mean TBT and reservoir sampling for P99 — O(1) per token.
    """

    __slots__ = (
        "_request_start",
        "_first_token_time",
        "_last_token_time",
        "_token_count",
        "_tbt_count",
        "_tbt_sum",
        "_tbt_reservoir",
        "_tbt_seen",
    )

    def __init__(self, request_start: float) -> None:
        self._request_start = request_start
        self._first_token_time: Optional[float] = None
        self._last_token_time: Optional[float] = None
        self._token_count = 0
        self._tbt_count = 0
        self._tbt_sum = 0.0
        self._tbt_reservoir: List[float] = []
        self._tbt_seen = 0

    @property
    def token_count(self) -> int:
        return self._token_count

    def on_content_token(self, now: float) -> None:
        if self._first_token_time is None:
            self._first_token_time = now
        elif self._last_token_time is not None:
            tbt = (now - self._last_token_time) * 1000.0
            self._tbt_sum += tbt
            self._tbt_count += 1
            self._tbt_seen += 1
            if len(self._tbt_reservoir) < _TBT_RESERVOIR_SIZE:
                self._tbt_reservoir.append(tbt)
            else:
                j = random.randint(0, self._tbt_seen - 1)
                if j < _TBT_RESERVOIR_SIZE:
                    self._tbt_reservoir[j] = tbt
        self._last_token_time = now
        self._token_count += 1

    def prometheus_tbt_samples(self) -> tuple[float, ...]:
        """Strided TBT samples for histogram observation (no extra allocation)."""
        if not self._tbt_reservoir:
            return ()
        if len(self._tbt_reservoir) <= _TBT_RESERVOIR_SIZE:
            return tuple(self._tbt_reservoir)
        step = max(1, len(self._tbt_reservoir) // _TBT_RESERVOIR_SIZE)
        return tuple(self._tbt_reservoir[::step])

    def finalize(self, now: float) -> LatencySnapshot:
        e2e_ms = (now - self._request_start) * 1000.0
        if self._first_token_time is not None:
            ttft_ms = (self._first_token_time - self._request_start) * 1000.0
        else:
            ttft_ms = e2e_ms

        mean_tbt = (self._tbt_sum / self._tbt_count) if self._tbt_count else None
        p99_tbt = percentile(self._tbt_reservoir, 0.99) if self._tbt_reservoir else None

        return LatencySnapshot(
            ttft_ms=ttft_ms,
            mean_tbt_ms=mean_tbt,
            p99_tbt_ms=p99_tbt,
            total_tokens=self._token_count,
            e2e_ms=e2e_ms,
        )
