#!/usr/bin/env python3
"""
Benchmark Script — vLLM Latency Metrics Proxy (Project 1A)
============================================================
Measures TTFT and TBT at three concurrency levels and produces
a markdown table suitable for a GitHub README or PR description.

Usage:
  # Against proxy (with metrics):
  python benchmarks/run_benchmark.py --base-url http://localhost:8080

  # Against raw vLLM (baseline, no metrics):
  python benchmarks/run_benchmark.py --base-url http://localhost:8000 --raw

  # Custom:
  python benchmarks/run_benchmark.py \
    --base-url http://localhost:8080 \
    --model facebook/opt-1.3b \
    --concurrency 1 5 20 \
    --requests-per-level 50 \
    --max-tokens 100

Outputs:
  benchmarks/results/benchmark_<timestamp>.json
  benchmarks/results/benchmark_<timestamp>.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class SingleResult:
    request_id: int
    concurrency: int
    ttft_ms: float | None
    mean_tbt_ms: float | None
    e2e_ms: float
    tokens_generated: int | None
    status_code: int
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.status_code == 200 and self.error is None


@dataclass
class LevelSummary:
    concurrency: int
    n_requests: int
    n_success: int
    throughput_rps: float
    ttft_p50: float | None
    ttft_p95: float | None
    ttft_p99: float | None
    ttft_mean: float | None
    mean_tbt_p50: float | None
    mean_tbt_p99: float | None
    e2e_p99: float
    error_rate: float


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------


async def single_streaming_request(
    client: httpx.AsyncClient,
    request_id: int,
    concurrency: int,
    model: str,
    max_tokens: int,
    prompt: str,
) -> SingleResult:
    t0 = time.monotonic()
    first_token_time: float | None = None
    token_times: list[float] = []
    status_code = 0
    error = None
    tokens_generated = 0

    try:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "stream": True,
            },
            timeout=120.0,
        ) as resp:
            status_code = resp.status_code
            async for line in resp.aiter_lines():
                now = time.monotonic()
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        payload = json.loads(line[6:])
                        choices = payload.get("choices", [])
                        if any(c.get("delta", {}).get("content") for c in choices):
                            if first_token_time is None:
                                first_token_time = now
                            token_times.append(now)
                            tokens_generated += 1
                    except json.JSONDecodeError:
                        pass
    except Exception as exc:
        error = str(exc)
        status_code = 0

    e2e_ms = (time.monotonic() - t0) * 1000.0
    ttft_ms = None
    mean_tbt_ms = None

    if first_token_time is not None:
        ttft_ms = (first_token_time - t0) * 1000.0

    if len(token_times) >= 2:
        tbts = [(token_times[i] - token_times[i - 1]) * 1000.0 for i in range(1, len(token_times))]
        mean_tbt_ms = statistics.mean(tbts)

    return SingleResult(
        request_id=request_id,
        concurrency=concurrency,
        ttft_ms=ttft_ms,
        mean_tbt_ms=mean_tbt_ms,
        e2e_ms=e2e_ms,
        tokens_generated=tokens_generated,
        status_code=status_code,
        error=error,
    )


async def run_level(
    base_url: str,
    model: str,
    concurrency: int,
    n_requests: int,
    max_tokens: int,
) -> list[SingleResult]:
    """Run n_requests with a given concurrency and return all results."""
    prompts = [
        "Explain the concept of attention in transformer models.",
        "What are the benefits of using Kubernetes for ML serving?",
        "Write a brief explanation of how LLM inference works.",
        "What is the difference between TTFT and TBT in LLM serving?",
        "How does paged attention reduce memory fragmentation in vLLM?",
    ]

    semaphore = asyncio.Semaphore(concurrency)
    results: list[SingleResult] = []

    async with httpx.AsyncClient(base_url=base_url) as client:

        async def bounded_request(rid: int) -> SingleResult:
            async with semaphore:
                prompt = prompts[rid % len(prompts)]
                return await single_streaming_request(
                    client, rid, concurrency, model, max_tokens, prompt
                )

        tasks = [bounded_request(i) for i in range(n_requests)]
        t_start = time.monotonic()
        results = await asyncio.gather(*tasks)
        elapsed = time.monotonic() - t_start

    return results, elapsed


def summarize(results: list[SingleResult], elapsed: float, concurrency: int) -> LevelSummary:
    successes = [r for r in results if r.success]
    ttfts = [r.ttft_ms for r in successes if r.ttft_ms is not None]
    mean_tbts = [r.mean_tbt_ms for r in successes if r.mean_tbt_ms is not None]
    e2es = [r.e2e_ms for r in successes]

    def pct(data, p):
        if not data:
            return None
        s = sorted(data)
        return round(s[max(0, int(len(s) * p) - 1)], 2)

    return LevelSummary(
        concurrency=concurrency,
        n_requests=len(results),
        n_success=len(successes),
        throughput_rps=round(len(successes) / elapsed, 2),
        ttft_p50=pct(ttfts, 0.50),
        ttft_p95=pct(ttfts, 0.95),
        ttft_p99=pct(ttfts, 0.99),
        ttft_mean=round(statistics.mean(ttfts), 2) if ttfts else None,
        mean_tbt_p50=pct(mean_tbts, 0.50),
        mean_tbt_p99=pct(mean_tbts, 0.99),
        e2e_p99=pct(e2es, 0.99) or 0,
        error_rate=round((len(results) - len(successes)) / len(results) * 100, 1),
    )


def render_markdown(summaries: list[LevelSummary], base_url: str, model: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# vLLM Latency Benchmark Results",
        "",
        f"**Timestamp:** {ts}  ",
        f"**Endpoint:** `{base_url}`  ",
        f"**Model:** `{model}`  ",
        "",
        "## TTFT (Time-To-First-Token) in milliseconds",
        "",
        "| Concurrency | Requests | Success | RPS | p50 | p95 | p99 | Mean |",
        "|-------------|----------|---------|-----|-----|-----|-----|------|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.concurrency} | {s.n_requests} | {s.n_success} "
            f"| {s.throughput_rps} "
            f"| {s.ttft_p50 or 'N/A'} "
            f"| {s.ttft_p95 or 'N/A'} "
            f"| {s.ttft_p99 or 'N/A'} "
            f"| {s.ttft_mean or 'N/A'} |"
        )

    lines += [
        "",
        "## TBT (Mean Time-Between-Tokens) in milliseconds",
        "",
        "| Concurrency | p50 | p99 | Error Rate |",
        "|-------------|-----|-----|------------|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.concurrency} "
            f"| {s.mean_tbt_p50 or 'N/A'} "
            f"| {s.mean_tbt_p99 or 'N/A'} "
            f"| {s.error_rate}% |"
        )

    lines += [
        "",
        "## Notes",
        "- TTFT measured client-side (proxy SSE comment or header)",
        "- TBT measured as mean inter-token interval per request",
        "- All values in milliseconds",
        "- Error rate includes timeouts and HTTP errors",
    ]
    return "\n".join(lines)


def compare_benchmarks(baseline_path: str, proxy_path: str) -> None:
    """Print overhead introduced by the latency proxy."""
    with open(baseline_path) as f:
        baseline = json.load(f)
    with open(proxy_path) as f:
        proxy = json.load(f)

    print("\n" + "=" * 60)
    print("Proxy Overhead Report")
    print("=" * 60)
    print(f"Baseline: {baseline_path}")
    print(f"With proxy: {proxy_path}\n")

    b_summaries = {s["concurrency"]: s for s in baseline["summaries"]}
    p_summaries = {s["concurrency"]: s for s in proxy["summaries"]}

    print(
        "| Concurrency | Baseline RPS | Proxy RPS | RPS delta% | Baseline TTFT p99 | Proxy TTFT p99 | TTFT delta ms |"
    )
    print(
        "|-------------|--------------|-----------|--------|-------------------|----------------|-----------|"
    )

    for conc in sorted(b_summaries.keys()):
        b = b_summaries.get(conc, {})
        p = p_summaries.get(conc, {})
        b_rps = b.get("throughput_rps", 0)
        p_rps = p.get("throughput_rps", 0)
        rps_delta = round((p_rps - b_rps) / b_rps * 100, 1) if b_rps else 0
        b_ttft = b.get("ttft_p99") or 0
        p_ttft = p.get("ttft_p99") or 0
        ttft_delta = round(p_ttft - b_ttft, 2)
        print(f"| {conc} | {b_rps} | {p_rps} | {rps_delta}% | {b_ttft} | {p_ttft} | {ttft_delta} |")

    print("\nNote: TTFT delta includes proxy network hop; RPS delta reflects overhead.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main():
    parser = argparse.ArgumentParser(description="vLLM Latency Benchmark")
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--model", default="facebook/opt-1.3b")
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 5, 20])
    parser.add_argument("--requests-per-level", type=int, default=50)
    parser.add_argument("--max-tokens", type=int, default=100)
    parser.add_argument("--output-dir", default="benchmarks/results")
    parser.add_argument(
        "--compare",
        nargs=2,
        metavar=("BASELINE_JSON", "WITH_PROXY_JSON"),
        help="Compare two benchmark JSON files and print overhead report",
    )
    args = parser.parse_args()

    if args.compare:
        compare_benchmarks(args.compare[0], args.compare[1])
        return

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"\n{'=' * 60}")
    print("vLLM Latency Benchmark")
    print(f"Endpoint: {args.base_url}")
    print(f"Model:    {args.model}")
    print(f"Levels:   concurrency={args.concurrency}")
    print(f"{'=' * 60}\n")

    all_summaries = []
    all_raw_results = []

    for concurrency in args.concurrency:
        print(f"Running concurrency={concurrency}, n={args.requests_per_level}...")
        results, elapsed = await run_level(
            base_url=args.base_url,
            model=args.model,
            concurrency=concurrency,
            n_requests=args.requests_per_level,
            max_tokens=args.max_tokens,
        )
        summary = summarize(results, elapsed, concurrency)
        all_summaries.append(summary)
        all_raw_results.extend([asdict(r) for r in results])

        print(
            f"  OK RPS={summary.throughput_rps}  "
            f"TTFT p99={summary.ttft_p99}ms  "
            f"TBT p99={summary.mean_tbt_p99}ms  "
            f"errors={summary.error_rate}%"
        )

    # Save JSON
    json_path = output_dir / f"benchmark_{ts}.json"
    with open(json_path, "w") as f:
        json.dump(
            {
                "meta": {"timestamp": ts, "base_url": args.base_url, "model": args.model},
                "summaries": [asdict(s) for s in all_summaries],
                "raw": all_raw_results,
            },
            f,
            indent=2,
        )

    # Save Markdown
    md_path = output_dir / f"benchmark_{ts}.md"
    md = render_markdown(all_summaries, args.base_url, args.model)
    with open(md_path, "w") as f:
        f.write(md)

    print(f"\n{'=' * 60}")
    print("Results saved:")
    print(f"  JSON: {json_path}")
    print(f"  MD:   {md_path}")
    print(f"\n{md}")


if __name__ == "__main__":
    asyncio.run(main())
