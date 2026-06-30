# Changelog

## [1.1.0] — 2026-06-29

### Added
- Shared `vllm_patch/latency_utils.py` module with `StreamLatencyTracker`,
  `LatencySnapshot`, and percentile helpers.
- Comprehensive test suite: edge cases, concurrent requests, regression tests.
- `docs/API.md` API reference.
- Configurable `PROXY_PORT` in Docker Compose.
- Windows-compatible uvicorn loop fallback (`asyncio` when `uvloop` unavailable).

### Fixed
- **ACTIVE_REQUESTS gauge leak** on streaming requests — gauge now decrements
  when the stream completes, not when the handler returns.
- **vLLM Docker health check** — replaced missing `curl` with Python probe.
- **Flaky TBT unit test** — uses synthetic timestamps instead of `sleep()`.
- Removed ~150 lines of duplicated streaming latency logic in `proxy.py`.

### Changed
- Proxy version bumped to 1.1.0.
- `LatencyMetrics.record_token_at()` added for deterministic testing.
- Tool-call and completion `text` deltas now counted as content tokens.

### Known Limitations
- Proxy-side TTFT includes network hop between proxy and vLLM.
- Non-streaming requests cannot measure per-token TBT.
- SSE latency headers are comments, not HTTP headers (HTTP/1.1 limitation).

## [1.0.0] — Initial Release

- Transparent latency proxy for vLLM OpenAI API.
- Prometheus metrics and Grafana dashboards.
- Docker Compose full stack.
- Kubernetes manifests and Helm chart.
