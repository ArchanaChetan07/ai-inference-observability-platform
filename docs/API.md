# API Reference — vLLM Latency Metrics Proxy

## Overview

The proxy wraps any OpenAI-compatible vLLM endpoint and adds **server-side
latency measurement** without modifying vLLM source code. All new fields are
**additive** — existing OpenAI clients remain compatible.

## Instrumented Endpoints

| Endpoint | Streaming | Latency injection |
|----------|-----------|-------------------|
| `POST /v1/chat/completions` | Yes | Headers + usage / SSE comments |
| `POST /v1/completions` | Yes | Headers + usage / SSE comments |
| `GET /health` | — | Proxy + upstream status |
| `GET /metrics` | — | Prometheus exposition |
| `GET /latency/stats` | — | Rolling p50/p95/p99 summary |
| All other paths | — | Transparent passthrough |

## Response Headers (non-streaming)

| Header | Type | Description |
|--------|------|-------------|
| `x-vllm-request-id` | string | Unique request identifier |
| `x-vllm-ttft-ms` | float | Time-to-first-token (ms) |
| `x-vllm-e2e-latency-ms` | float | End-to-end latency (ms) |
| `x-vllm-mean-tbt-ms` | float | Mean inter-token interval (streaming only) |
| `x-vllm-p99-tbt-ms` | float | P99 inter-token interval (streaming only) |
| `x-vllm-tokens-generated` | int | Content chunks observed (streaming only) |

## Usage Object Extensions

```json
{
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 47,
    "total_tokens": 59,
    "ttft_ms": 342.1,
    "mean_tbt_ms": null,
    "p99_tbt_ms": null,
    "e2e_latency_ms": 1823.4
  }
}
```

Non-streaming requests set `mean_tbt_ms` and `p99_tbt_ms` to `null` because
inter-token intervals are not observable without streaming.

## Streaming (SSE Comments)

At the end of each stream, before `[DONE]` processing completes, the proxy
appends SSE comment lines:

```
: x-vllm-ttft-ms=342.100
: x-vllm-mean-tbt-ms=31.400
: x-vllm-p99-tbt-ms=58.200
: x-vllm-tokens-generated=47
: x-vllm-e2e-latency-ms=1823.400
```

SSE comments are ignored by standard EventSource parsers but readable by
custom clients.

## Example — Non-Streaming

```bash
curl -si http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "facebook/opt-1.3b",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 32
  }'
```

## Example — Streaming

```bash
curl -N http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "facebook/opt-1.3b",
    "messages": [{"role": "user", "content": "Count to five."}],
    "max_tokens": 50,
    "stream": true
  }'
```

## Prometheus Metrics

| Metric | Type | Labels |
|--------|------|--------|
| `vllm_proxy_ttft_milliseconds` | Histogram | — |
| `vllm_proxy_tbt_milliseconds` | Histogram | — |
| `vllm_proxy_e2e_latency_seconds` | Histogram | — |
| `vllm_proxy_requests_total` | Counter | `endpoint`, `status` |
| `vllm_proxy_active_requests` | Gauge | — |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | `http://localhost:8000` | Upstream vLLM URL |
| `PROXY_HOST` | `0.0.0.0` | Bind address |
| `PROXY_PORT` | `8080` | Listen port |
| `STATS_WINDOW` | `1000` | Rolling stats window size |
| `LOG_LEVEL` | `INFO` | Log verbosity |

## Measurement Semantics

- **TTFT**: Time from proxy receiving the request to the first SSE chunk
  containing generative output (`delta.content`, `text`, or `tool_calls`).
- **TBT**: Inter-arrival time between consecutive content chunks at the proxy
  boundary (includes network + vLLM scheduling, not pure GPU kernel time).
- **Non-streaming TTFT**: Equals E2E latency (first token not separately observable).

For authoritative engine-side measurement, apply the upstream patches in
`vllm_patch/outputs.py` and `vllm_patch/engine_patch.py`.
