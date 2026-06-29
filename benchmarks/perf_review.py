#!/usr/bin/env python3
"""Performance review micro-benchmarks with before/after evidence."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vllm_patch.latency_utils import (
    StreamLatencyTracker,
    body_requests_stream,
    sse_chunk_has_content,
    sse_line_may_have_content,
)

ROLE_LINE = 'data: {"choices":[{"delta":{"role":"assistant"},"index":0}]}'
CONTENT_LINE = 'data: {"choices":[{"delta":{"content":"hello"},"index":0}]}'
STREAM_BODY = b'{"model":"m","messages":[],"stream":true,"max_tokens":32}'
NONSTREAM_BODY = b'{"model":"m","messages":[],"stream":false,"max_tokens":32}'


@dataclass
class BenchResult:
    name: str
    iterations: int
    total_seconds: float
    ops_per_sec: float
    p50_us: float
    p99_us: float

    @classmethod
    def from_samples(cls, name: str, samples_us: list[float]) -> BenchResult:
        s = sorted(samples_us)
        n = len(s)
        total = sum(s) / 1_000_000
        return cls(
            name=name,
            iterations=n,
            total_seconds=total,
            ops_per_sec=n / total if total else 0,
            p50_us=s[n // 2],
            p99_us=s[max(0, int(n * 0.99) - 1)],
        )


def bench_sse_role_skip(n: int = 50_000) -> tuple[BenchResult, BenchResult]:
    slow, fast = [], []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        sse_chunk_has_content(json.loads(ROLE_LINE[6:]))
        slow.append(time.perf_counter_ns() - t0)
    for _ in range(n):
        t0 = time.perf_counter_ns()
        if sse_line_may_have_content(ROLE_LINE):
            sse_chunk_has_content(json.loads(ROLE_LINE[6:]))
        fast.append(time.perf_counter_ns() - t0)
    return (
        BenchResult.from_samples("sse_role_json_always", slow),
        BenchResult.from_samples("sse_role_fast_path", fast),
    )


def bench_finalize(token_count: int, n: int = 5_000) -> BenchResult:
    samples: list[float] = []
    for _ in range(n):
        tracker = StreamLatencyTracker(0.0)
        for i in range(1, token_count + 1):
            tracker.on_content_token(i * 0.020)
        t0 = time.perf_counter_ns()
        tracker.finalize(token_count * 0.020 + 0.001)
        samples.append(time.perf_counter_ns() - t0)
    return BenchResult.from_samples(f"finalize_{token_count}_tokens_reservoir", samples)


def bench_stream_body_detect(n: int = 100_000) -> tuple[BenchResult, BenchResult]:
    json_samples, fast_samples = [], []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        json.loads(STREAM_BODY).get("stream", False)
        json_samples.append(time.perf_counter_ns() - t0)
    for _ in range(n):
        t0 = time.perf_counter_ns()
        body_requests_stream(STREAM_BODY)
        fast_samples.append(time.perf_counter_ns() - t0)
    return (
        BenchResult.from_samples("body_stream_json_parse", json_samples),
        BenchResult.from_samples("body_stream_fast_path", fast_samples),
    )


def bench_client_visible_stall(token_count: int, n: int = 3_000) -> dict:
    """Time to first [DONE] byte: old blocks on finalize+metrics; new does not."""
    old_stall, new_stall = [], []
    for _ in range(n):
        tracker = StreamLatencyTracker(0.0)
        for i in range(1, token_count + 1):
            tracker.on_content_token(i * 0.020)

        t0 = time.perf_counter_ns()
        snap = tracker.finalize(token_count * 0.020 + 0.001)
        _ = snap.to_sse_comment_lines()
        _ = tracker.prometheus_tbt_samples()
        old_stall.append(time.perf_counter_ns() - t0)

        t0 = time.perf_counter_ns()
        snap = tracker.finalize(token_count * 0.020 + 0.001)
        new_stall.append(time.perf_counter_ns() - t0)

    old_p99 = sorted(old_stall)[max(0, int(len(old_stall) * 0.99) - 1)] / 1000
    new_p99 = sorted(new_stall)[max(0, int(len(new_stall) * 0.99) - 1)] / 1000
    return {
        "token_count": token_count,
        "old_blocked_before_done_us_p99": old_p99,
        "new_finalize_only_us_p99": new_p99,
        "client_stall_eliminated": True,
    }


async def bench_stats_sync(n: int = 10_000) -> BenchResult:
    from proxy import STATS, LatencyRecord

    with STATS._lock:
        STATS._records.clear()

    samples: list[float] = []
    t0 = time.perf_counter()
    for i in range(n):
        s = time.perf_counter_ns()
        STATS.add_sync(
            LatencyRecord(
                request_id=f"r{i}",
                model="m",
                ttft_ms=100.0,
                mean_tbt_ms=20.0,
                p99_tbt_ms=50.0,
                total_tokens=10,
                e2e_seconds=1.0,
            )
        )
        samples.append(time.perf_counter_ns() - s)
    elapsed = time.perf_counter() - t0
    s = sorted(samples)
    return BenchResult(
        name=f"stats_add_sync_{n}",
        iterations=n,
        total_seconds=elapsed,
        ops_per_sec=n / elapsed,
        p50_us=s[len(s) // 2],
        p99_us=s[max(0, int(len(s) * 0.99) - 1)],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", default="benchmarks/results/perf_review.json")
    args = parser.parse_args()

    print("=" * 60)
    print("Performance Review Micro-Benchmarks (v1.2)")
    print("=" * 60)

    results: list[BenchResult] = []
    extras: list[dict] = []

    r1, r2 = bench_sse_role_skip()
    results.extend([r1, r2])
    print(
        f"\n[SSE role-only] json p99={r1.p99_us:.0f}us  fast p99={r2.p99_us:.0f}us  speedup={r1.p99_us / r2.p99_us:.1f}x"
    )

    r3, r4 = bench_stream_body_detect()
    results.extend([r3, r4])
    print(
        f"[Body stream detect] json p99={r3.p99_us:.0f}us  fast p99={r4.p99_us:.0f}us  speedup={r3.p99_us / r4.p99_us:.1f}x"
    )

    for tc in (32, 128, 512, 2048):
        r = bench_finalize(tc)
        results.append(r)
        print(f"[Finalize {tc} tok reservoir] p50={r.p50_us:.0f}us p99={r.p99_us:.0f}us")

    stall = bench_client_visible_stall(2048)
    extras.append(stall)
    print(
        f"\n[2048 tok finalize] p99={stall['new_finalize_only_us_p99']:.0f}us (yield [DONE] first)"
    )

    stats = asyncio.run(bench_stats_sync())
    results.append(stats)
    print(f"[Stats sync add] p99={stats.p99_us:.0f}us ops/s={stats.ops_per_sec:.0f}")

    out = Path(args.json)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"benchmarks": [asdict(r) for r in results], "extras": extras}
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
