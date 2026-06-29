# Production Gap Analysis — AI Inference Observability Platform

**Generated:** 2026-06-29  
**Baseline version:** 1.2.0

---

## Scoring Legend

| Rank | Meaning |
|------|---------|
| **Critical** | Blocks production deployment or poses security risk |
| **High** | Required for enterprise SLO/ops; workaround exists |
| **Medium** | Best practice gap; acceptable for MVP |
| **Low** | Nice-to-have, polish, or documentation |

---

## Critical

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| C1 | Proxy image not published to GHCR | **Open** | K8s pods fail `ErrImagePull` until CI push or manual `docker push` |
| C2 | GitHub push requires auth | **Blocked** | User must run `gh auth login` or configure PAT |
| C3 | vLLM Compose health flaps on slow model load | **Mitigated** | `start_period` increased to 300s; hybrid K8s pattern documented |
| C4 | GPU memory exhaustion causes AsyncEngineDeadError | **Open** | Operational: reduce `gpu_memory_utilization` or model size |

---

## High

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| H1 | Alertmanager not deployed | Open | `alerts.yml` exists; no Alertmanager in Compose/Helm |
| H2 | No Pod Security Admission labels on namespace | Open | Add `pod-security.kubernetes.io/enforce: restricted` |
| H3 | E2E tests not in CI | Open | Require live vLLM; run in nightly job |
| H4 | OTel end-to-end not validated in CI | Open | Collector template exists; needs integration test |
| H5 | Deprecated nested Helm chart | Open | `helm/vllm-latency-metrics/` — remove after migration |
| H6 | K8s hybrid Prometheus scrape for Compose proxy | Open | Add static scrape target for `host.docker.internal` |
| H7 | TLS/Ingress not configured by default | Open | `ingress.enabled: false`; needs cert-manager in prod |
| H8 | No rate limiting / auth on proxy | Open | Acceptable for internal mesh; add API gateway for public |

---

## Medium

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| M1 | HPA on custom TTFT metrics | Open | HPA uses CPU; needs Prometheus adapter |
| M2 | DCGM GPU dashboards | Open | Node-level GPU metrics not wired |
| M3 | k6/Locust load test harness | Open | Python benchmark exists; no k6 scripts |
| M4 | Chaos engineering tests | Open | Not applicable without staging cluster |
| M5 | Container image signing (cosign) | Open | SBOM step added; signing not wired |
| M6 | Loki log aggregation | Open | stdout only; no centralized logging |
| M7 | Tempo/Jaeger in default Helm values | Partial | OTel disabled by default |
| M8 | PriorityClass for inference pods | Open | Not defined |
| M9 | Topology spread constraints | Open | Affinity empty in values |
| M10 | Duplicate legacy `k8s/manifests.yaml` | Open | Kept for backward compat with deprecation note |

---

## Low

| ID | Gap | Status | Notes |
|----|-----|--------|-------|
| L1 | Helm Chart.yaml missing icon | Open | Lint INFO only |
| L2 | Makefile defaults PROXY_PORT 8080 | Open | Compose now defaults 8082 |
| L3 | Upstream vLLM patch merge | Roadmap | Standalone proxy avoids fork |
| L4 | Markdown link lint in docs | Open | No markdownlint in CI |
| L5 | Hadolint for Dockerfile | Open | Manual review done; not in CI |
| L6 | ShellCheck for scripts | Open | PowerShell primary on Windows |

---

## Security Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| HF token in env/Secret misconfiguration | High | Document Secret creation; never commit tokens |
| Grafana default password `admin` | Medium | Override in prod values |
| No mTLS between proxy and vLLM | Medium | NetworkPolicy + internal ClusterIP |
| Dependency CVEs | Medium | pip-audit + trivy in CI |
| Exposed LoadBalancer in default Helm values | Medium | Use ClusterIP + Ingress in prod |

---

## Performance Bottlenecks

| Area | Issue | Recommendation |
|------|-------|----------------|
| Single uvicorn worker | CPU-bound header parsing | Scale proxy replicas via HPA |
| STATS_WINDOW in-memory deque | Memory grows with window | Bounded deque (already 1000) |
| Synchronous percentile calc | Minor under load | Acceptable at current scale |
| vLLM model cold start | 2–5 min on OPT-1.3b | Startup probe + longer health start_period |

---

## Technical Debt

1. **Dual Helm chart paths** — root `helm/` vs deprecated `helm/vllm-latency-metrics/`
2. **CI template duplicate** — `ci/.github_workflows_main.yml` superseded by `.github/workflows/main.yml`
3. **Monolithic legacy manifest** — `k8s/manifests.yaml` vs Kustomize split
4. **Windows-first scripts** — `deploy-windows.ps1`; no equivalent bash deploy script

---

## Implemented in This Pass (2026-06-29)

- Docker Compose: removed obsolete `version`, PROXY_PORT default 8082, vLLM `start_period` 300s
- Dockerfile typo fix, OCI labels, `.dockerignore`
- K8s: ServiceAccount, NetworkPolicy, startup probes, GHCR image refs
- Helm: GPU conditional resources, values-prod/dev, serviceaccount, networkpolicy, PDB fix
- CI: `.github/workflows/main.yml` with correct `./helm` paths, SBOM, pip-audit, kustomize validate
- Placeholder `your-username` → `ArchanaChetan07`
- `ruff.toml` for lint configuration
- Reports directory with analysis artifacts

---

## Priority Roadmap

1. **Publish image to GHCR** (unblocks K8s proxy)
2. **Deploy Alertmanager** + wire notification channels
3. **Enable Pod Security Standards** on `vllm` namespace
4. **Prometheus Adapter** for TTFT-based HPA
5. **Remove deprecated chart** and legacy monolithic manifest
6. **Nightly E2E** job with self-hosted GPU runner
