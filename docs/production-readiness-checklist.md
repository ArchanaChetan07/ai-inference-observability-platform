# Production Readiness Checklist

## Application

- [x] OpenAI API compatibility preserved (`/v1/chat/completions`, `/v1/completions`)
- [x] Health endpoint with upstream check (`/health`)
- [x] Prometheus metrics (`/metrics`)
- [x] Non-root container, read-only root FS
- [x] Graceful streaming (yield `[DONE]` before metrics)
- [x] OpenTelemetry optional instrumentation
- [x] Unit + integration tests (51+)

## Kubernetes

- [x] Modular manifests (Namespace, ConfigMap, Deployments, Services, HPA, PDB)
- [x] GPU scheduling (requests/limits, nodeSelector, tolerations)
- [x] Liveness and readiness probes
- [x] Rolling update strategy
- [x] Resource requests and limits
- [ ] NetworkPolicies (recommended for hardening)
- [ ] Pod Security Standards / restricted SCC

## Helm

- [x] Configurable model, ports, resources, replicas, GPU count, image tag
- [x] Prometheus / Grafana enable flags
- [x] OpenTelemetry enable flag
- [x] NOTES.txt post-install guide
- [ ] Chart signed / OCI registry publish

## Observability

- [x] Prometheus histograms (TTFT, TBT, E2E)
- [x] Grafana dashboard JSON in `monitoring/`
- [x] OpenTelemetry OTLP export
- [ ] Alertmanager rules deployed in cluster
- [ ] SLO dashboards (Tempo + Prometheus correlation)

## Operations

- [x] Docker Compose local stack
- [x] Multi-proxy LB demo (`docker-compose.multi.yml`)
- [x] Deployment and troubleshooting docs
- [ ] Runbook for model hot-swap
- [ ] Backup strategy for HF cache PVC

## Security

- [x] HF token via K8s Secret (not in ConfigMap)
- [ ] Image scanning in CI
- [ ] SBOM generation
- [ ] mTLS proxy ↔ vLLM (service mesh optional)

## CI/CD

- [x] GitHub Actions workflow template in `ci/`
- [ ] Automated helm lint + kubeconform in CI
- [ ] Container image publish on tag

## Score Target

Current self-assessment: **9.0 / 10** — see README for full review.
