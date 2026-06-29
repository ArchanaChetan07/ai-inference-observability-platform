<div align="center">

# AI Inference Observability Platform

**Production-grade latency instrumentation for vLLM — TTFT, TBT, and end-to-end metrics in every API response.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![vLLM](https://img.shields.io/badge/vLLM-Inference-blue)](https://github.com/vllm-project/vllm)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](docker/docker-compose.yml)
[![Kubernetes](https://img.shields.io/badge/Kubernetes-326CE5?logo=kubernetes&logoColor=white)](k8s/)
[![Helm](https://img.shields.io/badge/Helm-0F1689?logo=helm&logoColor=white)](helm/)
[![Prometheus](https://img.shields.io/badge/Prometheus-E6522C?logo=prometheus&logoColor=white)](monitoring/)
[![Grafana](https://img.shields.io/badge/Grafana-F46800?logo=grafana&logoColor=white)](monitoring/)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-OTLP-000000?logo=opentelemetry&logoColor=white)](docs/opentelemetry.md)
[![CI/CD](https://github.com/ArchanaChetan07/ai-inference-observability-platform/actions/workflows/main.yml/badge.svg)](https://github.com/ArchanaChetan07/ai-inference-observability-platform/actions/workflows/main.yml)
[![Tests](https://img.shields.io/badge/Tests-48_passing-success)](tests/)

[Quick Start](#quick-start) · [Architecture](#architecture) · [Observability](#observability) · [Deployment](#production-deployment) · [Documentation](#documentation)

</div>

---

## Overview

Large language model serving is judged on **responsiveness** — how fast the first token arrives (**TTFT**) and how smoothly tokens stream (**TBT**). [vLLM](https://github.com/vllm-project/vllm) optimizes GPU throughput internally, but its OpenAI-compatible API does not expose per-request latency to clients.

**AI Inference Observability Platform** closes that gap with a transparent FastAPI proxy that wraps any vLLM endpoint and surfaces authoritative latency metrics — without modifying client code or forking vLLM.

| | |
|---|---|
| **Deploy time** | ~2 minutes (Docker Compose, includes model download) |
| **Proxy overhead** | ≤ 4% RPS · ≤ 31 ms TTFT P99 @ concurrency 5 |
| **Test coverage** | 48 automated tests (unit · integration · regression · concurrent) |
| **Production stack** | Kubernetes · Helm · Prometheus · Grafana · Alertmanager · OpenTelemetry |
| **Production readiness** | [100 / 100](reports/production-readiness-100.md) |

---

## Why teams use this

| Challenge | How this platform solves it |
|-----------|----------------------------|
| No server-side TTFT/TBT in vLLM responses | Injects metrics into headers, `usage` fields, and SSE comments |
| Inconsistent client-side timing | Single source of truth at the HTTP boundary |
| No SLO dashboards out of the box | Prometheus histograms + Grafana dashboard + alert rules |
| Hard to debug latency spikes | Optional OpenTelemetry traces with per-request breakdown |
| Production deployment complexity | Modular K8s manifests, Helm chart, HPA, GPU scheduling |

---

## Key features

- **OpenAI-compatible** — `/v1/chat/completions` and `/v1/completions` with zero client changes
- **Streaming-first** — SSE passthrough; latency comments after `data: [DONE]` (never blocks the terminal chunk)
- **Three metric layers** — TTFT · mean/P99 TBT · end-to-end latency on every request
- **Full observability** — Prometheus `/metrics` · Grafana dashboards · Alertmanager · OTLP traces (Jaeger / Tempo)
- **Production-ready** — Docker Compose · Kustomize · Helm · HPA · PDB · NetworkPolicy · GPU node scheduling
- **Security hardened** — multi-stage Docker · pinned apt/pip deps · non-root containers · CI vulnerability scanning
- **Benchmarked** — reproducible E2E and micro-benchmark suite with published results
- **Optional upstream patch** — annotated vLLM engine integration for GPU-authoritative measurement ([`vllm_patch/`](vllm_patch/))

---

## Technology stack

| Layer | Technologies |
|-------|-------------|
| **Application** | Python 3.10+ · FastAPI · httpx · uvicorn · uvloop · SSE |
| **Inference** | vLLM · NVIDIA GPU · HuggingFace models |
| **Containers** | Docker · multi-stage builds · Docker Compose |
| **Orchestration** | Kubernetes · Helm · Kustomize · HPA · PDB · NetworkPolicy |
| **Observability** | Prometheus · Grafana · Alertmanager · OpenTelemetry · Jaeger |
| **CI/CD** | GitHub Actions · GHCR · Ruff · mypy · Hadolint · pytest · Trivy · Cosign · kubeconform |

---

## Architecture

```mermaid
flowchart LR
    subgraph Clients
        SDK[OpenAI SDK / curl / LangChain]
    end
    subgraph Platform
        Proxy[Latency Proxy<br/>FastAPI]
        Prom[Prometheus]
        AM[Alertmanager]
        Graf[Grafana]
        OTel[OpenTelemetry]
    end
    subgraph Inference
        VLLM[vLLM Server]
        GPU[NVIDIA GPU]
    end
    SDK --> Proxy
    Proxy --> VLLM --> GPU
    Proxy --> Prom --> AM
    Prom --> Graf
    Proxy -.-> OTel
```

**Request flow (streaming):**

1. Client sends `POST /v1/chat/completions` to the proxy
2. Proxy forwards transparently to vLLM and tracks token arrival timestamps
3. Client receives SSE chunks in real time — no added latency on the hot path
4. After `data: [DONE]`, proxy appends SSE comment lines with TTFT/TBT/E2E
5. Prometheus histograms updated; optional OTLP trace exported

| Component | Role |
|-----------|------|
| [`proxy.py`](proxy.py) | Production FastAPI sidecar (v1.2) |
| [`vllm_patch/latency_utils.py`](vllm_patch/latency_utils.py) | O(1) per-token tracker with reservoir P99 |
| [`vllm_patch/telemetry.py`](vllm_patch/telemetry.py) | Optional OpenTelemetry OTLP export |
| [`docker/`](docker/) | Multi-stage Dockerfile · Compose · Alertmanager · OTel overlay |
| [`k8s/`](k8s/) · [`helm/`](helm/) | Production Kubernetes deployment |
| [`monitoring/`](monitoring/) | Grafana dashboard · Prometheus alert rules |
| [`.github/workflows/main.yml`](.github/workflows/main.yml) | CI/CD pipeline |

Full API reference: [`docs/API.md`](docs/API.md)

---

## Example output

### Non-streaming — response headers

```http
HTTP/1.1 200 OK
x-vllm-request-id: req-a1b2c3d4
x-vllm-ttft-ms: 342.1
x-vllm-e2e-latency-ms: 1823.4
Content-Type: application/json
```

### Streaming — SSE comments (after `[DONE]`)

```
data: [DONE]
: x-vllm-ttft-ms=188.000
: x-vllm-mean-tbt-ms=142.790
: x-vllm-p99-tbt-ms=143.860
: x-vllm-tokens-generated=32
: x-vllm-e2e-latency-ms=4375.000
```

### Extended `usage` object

```json
{
  "usage": {
    "prompt_tokens": 12,
    "completion_tokens": 32,
    "total_tokens": 44,
    "ttft_ms": 188.0,
    "mean_tbt_ms": 142.79,
    "p99_tbt_ms": 143.86,
    "e2e_latency_ms": 4375.0
  }
}
```

---

## Quick start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) with [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html) (GPU)
- HuggingFace token optional for public models (`facebook/opt-1.3b`)

### Run the full stack

```bash
git clone https://github.com/ArchanaChetan07/ai-inference-observability-platform.git
cd ai-inference-observability-platform

docker compose -f docker/docker-compose.yml up -d --build
# Wait ~2 min for vLLM to load weights, then:
curl -s http://localhost:8082/health | python -m json.tool
```

### Send your first instrumented request

```bash
curl -N http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "facebook/opt-1.3b",
    "messages": [{"role": "user", "content": "Explain TTFT in one sentence."}],
    "max_tokens": 32,
    "stream": true
  }'
```

### Service endpoints

| Service | URL | Purpose |
|---------|-----|---------|
| **Proxy** (use this) | http://localhost:8082 | OpenAI API + latency metrics |
| vLLM (raw) | http://localhost:8000 | Upstream inference server |
| Prometheus | http://localhost:9090 | Metrics collection |
| Alertmanager | http://localhost:9093 | Alert routing |
| Grafana | http://localhost:3000 | Dashboards (`admin` / `admin`) |

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
git clone https://github.com/ArchanaChetan07/ai-inference-observability-platform.git
cd ai-inference-observability-platform
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8082/health
```

</details>

---

## Observability

### Prometheus metrics

| Metric | Type | Description |
|--------|------|-------------|
| `vllm_proxy_ttft_milliseconds` | Histogram | Time to first token |
| `vllm_proxy_tbt_milliseconds` | Histogram | Inter-token interval |
| `vllm_proxy_e2e_latency_seconds` | Histogram | End-to-end latency |
| `vllm_proxy_requests_total` | Counter | Requests by endpoint + status |
| `vllm_proxy_active_requests` | Gauge | In-flight requests |

```promql
histogram_quantile(0.99, rate(vllm_proxy_ttft_milliseconds_bucket[5m]))
sum(rate(vllm_proxy_requests_total{status="200"}[1m]))
```

Alert rules: [`monitoring/alerts.yml`](monitoring/alerts.yml) · Alertmanager: [`docker/alertmanager.yml`](docker/alertmanager.yml)

### OpenTelemetry (optional)

```bash
docker compose -f docker/docker-compose.yml -f docker/docker-compose.otel.yml up -d --build
# Jaeger UI: http://localhost:16686
```

Details: [`docs/opentelemetry.md`](docs/opentelemetry.md)

---

## Benchmarks

**Environment:** NVIDIA T1000 8 GB · `facebook/opt-1.3b` · 30 tokens · streaming  
**Artifacts:** [`benchmarks/results/`](benchmarks/results/)

### End-to-end: vLLM direct vs proxy

| Concurrency | Endpoint | Req/s | TTFT P99 | Overhead |
|:-----------:|----------|------:|---------:|---------:|
| 1 | vLLM `:8000` | 0.24 | 203 ms | — |
| 1 | Proxy `:8082` | 0.23 | 203 ms | −4.2% RPS |
| 5 | vLLM `:8000` | 1.03 | 813 ms | — |
| 5 | Proxy `:8082` | 1.02 | 844 ms | +31 ms P99 |

**Conclusion:** GPU inference and vLLM batch scheduling dominate latency — not proxy overhead.

```bash
python benchmarks/run_benchmark.py --base-url http://localhost:8082 --concurrency 1 5
python benchmarks/perf_review.py
```

---

## Production deployment

```mermaid
flowchart TB
    Client --> LB[LoadBalancer / Ingress]
    LB --> Proxy[Proxy Pods ×2–10]
    Proxy --> VLLM[vLLM GPU Pod]
    HPA[HPA] --> Proxy
    Proxy --> Prom[Prometheus]
    Prom --> AM[Alertmanager]
    Prom --> Graf[Grafana]
```

### Kubernetes (Kustomize)

```bash
kubectl create namespace vllm
kubectl create secret generic hf-token --from-literal=HF_TOKEN=$HF_TOKEN -n vllm
kubectl apply -k k8s/
kubectl get svc vllm-latency-proxy -n vllm
```

### Helm

```bash
# Production GPU cluster
helm upgrade --install latency-metrics ./helm -n vllm --create-namespace \
  -f helm/values-prod.yaml

# Docker Desktop hybrid (vLLM in Compose, monitoring in K8s)
helm upgrade --install latency-metrics ./helm -n vllm \
  -f helm/values-docker-desktop.yaml
```

### CI/CD

Every push to `main` triggers [GitHub Actions](.github/workflows/main.yml):

| Stage | Tools |
|-------|-------|
| Lint | Ruff · mypy · Hadolint |
| Test | pytest matrix (Python 3.10–3.12) · coverage |
| Validate | Helm lint · kubeconform · offline manifest dry-run |
| Security | Bandit · pip-audit · Trivy (filesystem SARIF + SBOM) |
| Build | Docker multi-stage · GHCR push · Cosign keyless signing |
| Scan | Trivy container SARIF · container SBOM |

Docker builds use pinned apt packages and [`requirements.lock`](requirements.lock) for reproducible installs. Regenerate the lock file when dependencies change:

```bash
pip-compile requirements.txt -o requirements.lock --strip-extras
```

> Route all client traffic through the **proxy** Service — not vLLM directly.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `VLLM_BASE_URL` | `http://vllm:8000` | Upstream vLLM endpoint |
| `PROXY_PORT` | `8080` (host `8082` in Compose) | Proxy listen port |
| `VLLM_MODEL` | `facebook/opt-1.3b` | Model name (Compose) |
| `HF_TOKEN` | — | HuggingFace access token |
| `STATS_WINDOW` | `1000` | Rolling stats window size |
| `OTEL_ENABLED` | `false` | Enable OpenTelemetry tracing |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP collector endpoint |

---

## Testing

```bash
pip install -r requirements-dev.txt
pytest tests/ -m "unit or integration or regression" -v   # 48 tests, no GPU
VLLM_E2E_URL=http://localhost:8082 pytest tests/ -m e2e   # live stack required
powershell -File scripts/validate.ps1                      # full validation suite
```

| Suite | Marker | Coverage |
|-------|--------|----------|
| Unit | `unit` | Percentiles, tracker, SSE fast-path |
| Integration | `integration` | Headers, usage, mocked upstream |
| Concurrent | `integration` | 20 parallel requests, gauge leak |
| E2E | `e2e` | Live vLLM TTFT + SSE comments |
| Telemetry | `unit` | OpenTelemetry noop path |

---

## Project structure

```
ai-inference-observability-platform/
├── proxy.py                      # FastAPI latency proxy
├── requirements.lock             # Pinned deps for reproducible Docker builds
├── vllm_patch/                   # Latency utils + OpenTelemetry + upstream patch
├── docker/                       # Dockerfile, Compose, Alertmanager, OTel overlay
├── k8s/                          # Kubernetes manifests (Kustomize)
├── helm/                         # Helm chart (prod · dev · docker-desktop values)
├── monitoring/                   # Grafana dashboard, Prometheus alert rules
├── benchmarks/                   # E2E + micro-benchmark harness
├── tests/                        # Pytest suite
├── .github/workflows/main.yml    # CI/CD pipeline
├── pyproject.toml                # Ruff, mypy, pytest configuration
└── docs/                         # Deployment, architecture, runbooks
```

---

## Documentation

| Guide | Description |
|-------|-------------|
| [Deployment guide](docs/deployment-guide.md) | All deployment paths |
| [Kubernetes guide](docs/k8s-deployment.md) | Manifests, scaling, probes |
| [Multi-node architecture](docs/multi-node-architecture.md) | TP/PP, routing, KV cache |
| [OpenTelemetry](docs/opentelemetry.md) | Distributed tracing setup |
| [Troubleshooting (K8s)](docs/troubleshooting-k8s.md) | Common cluster issues |
| [Production checklist](docs/production-readiness-checklist.md) | Pre-launch checklist |
| [API reference](docs/API.md) | Endpoints, headers, metrics |

---

## Upstream vLLM integration

For teams contributing latency metrics upstream, the platform includes an annotated patch targeting vLLM's `RequestOutput`, async engine, and OpenAI serving layer.

| vLLM file | Change |
|-----------|--------|
| `vllm/outputs.py` | `LatencyMetrics` dataclass on `RequestOutput` |
| `vllm/engine/async_llm_engine.py` | Per-token timestamp recording |
| `vllm/entrypoints/openai/serving_chat.py` | Header + usage injection |

PR template: [`docs/PR_DESCRIPTION.md`](docs/PR_DESCRIPTION.md) · Annotated diffs: [`vllm_patch/engine_patch.py`](vllm_patch/engine_patch.py)

---

## Roadmap

- [x] OpenTelemetry distributed tracing
- [x] Kubernetes manifests + Helm chart (prod/dev/desktop overlays)
- [x] GitHub Actions CI/CD with security scanning
- [x] Alertmanager + Prometheus alert rules
- [x] NetworkPolicy + Pod Security Standards
- [ ] Upstream merge into vLLM core
- [ ] HPA on custom TTFT Prometheus metrics
- [ ] DCGM GPU panels in Grafana
- [ ] k6 / Locust load-test harness

---

## Contributing

Contributions are welcome. Please:

1. Fork the repository and create a feature branch
2. Add tests for new behaviour (`pytest tests/ -v`)
3. Run `ruff check .` and `ruff format --check .` before submitting
4. Include benchmark output for performance changes
5. Open a pull request with a clear description

See [CHANGELOG.md](CHANGELOG.md) for release history.

---

## License

This project is licensed under the [MIT License](LICENSE).

---

## Acknowledgements

Built on [vLLM](https://github.com/vllm-project/vllm) · [FastAPI](https://fastapi.tiangolo.com/) · [Prometheus](https://prometheus.io/) · [Grafana](https://grafana.com/) · [OpenTelemetry](https://opentelemetry.io/)

---

<div align="center">

Maintained by [ArchanaChetan07](https://github.com/ArchanaChetan07)

**[⭐ Star this repo](https://github.com/ArchanaChetan07/ai-inference-observability-platform)** if it helps your inference observability stack.

</div>
