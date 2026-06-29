# PR: [Frontend] Per-request TTFT/TBT latency metrics in API response

## Summary

Adds server-side latency measurement to vLLM's API layer. Every instrumented
request reports `ttft_ms`, `mean_tbt_ms`, and `p99_tbt_ms` in the `usage`
object and as response headers (`x-vllm-ttft-ms`, etc.).

This PR includes:
1. **Upstream patch artifacts** (`vllm_patch/`) for direct vLLM integration
2. **Deployment proxy** (`proxy.py`) for zero-friction adoption on any vLLM version

## Motivation

Users must currently instrument their own clients to measure TTFT/TBT, leading
to inconsistent methodology. Server-side measurement is authoritative and enables
SLO enforcement via Prometheus/Grafana.

## Design Rationale

- **Additive only**: New `usage` fields and headers; existing clients unchanged.
- **Monotonic timestamps**: All latency math uses `time.monotonic()` deltas.
- **Lazy finalization**: TBT stats computed once at stream/request end.
- **Shared abstractions**: `LatencyMetrics` (engine) and `StreamLatencyTracker`
  (proxy) share percentile/TBT helpers via `latency_utils.py`.
- **Minimal hot-path cost**: O(1) append per token; finalize is O(n log n) for
  p99 on n tokens (negligible vs inference).

## Changes

| File | Change |
|------|--------|
| `vllm_patch/outputs.py` | `LatencyMetrics` dataclass + `RequestOutput.latency` |
| `vllm_patch/latency_utils.py` | Shared percentile, TBT, SSE helpers |
| `vllm_patch/engine_patch.py` | Annotated engine integration guide |
| `proxy.py` | Production proxy with Prometheus + rolling stats |
| `tests/` | Unit, integration, concurrent, edge, regression, e2e |
| `docs/API.md` | API reference |
| `docker/docker-compose.yml` | Fixed health check, configurable port |

## Testing Summary

```
pytest tests/ -m "unit or integration or regression" -v
```

| Category | Count | Coverage |
|----------|-------|----------|
| Unit | 25+ | LatencyMetrics, StreamLatencyTracker, RollingStats |
| Integration | 15+ | Headers, usage, SSE, passthrough, errors |
| Concurrent | 3 | Parallel streaming/non-streaming, gauge leak |
| Edge cases | 6 | Malformed JSON, empty streams, tool_calls |
| Regression | 1 | OpenAI usage fields preserved |
| E2E | 3 | Real vLLM (requires `VLLM_E2E_URL`) |

## Benchmark Summary

Run before/after comparison:

```bash
python benchmarks/run_benchmark.py --base-url http://localhost:8000 --output-dir benchmarks/results/baseline
python benchmarks/run_benchmark.py --base-url http://localhost:8080 --output-dir benchmarks/results/with-proxy
python benchmarks/run_benchmark.py --compare benchmarks/results/baseline/benchmark_*.json benchmarks/results/with-proxy/benchmark_*.json
```

Expected proxy overhead: **< 1ms** per non-streaming request (JSON parse +
header injection); **< 0.1ms** per streaming chunk (timestamp append only).

## Known Limitations

1. Proxy TTFT includes proxy↔vLLM network latency (~0.1–2ms local).
2. Non-streaming TBT fields are always `null`.
3. Streaming latency in HTTP headers requires SSE comments (not standard headers).
4. Concurrent `asyncio.create_task(STATS.add())` may reorder stats window slightly.

## Backward Compatibility

✅ All existing OpenAI fields preserved  
✅ New fields are optional/nullable  
✅ Passthrough routes unmodified  
✅ No vLLM version requirement for proxy deployment
