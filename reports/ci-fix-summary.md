# CI/CD Fix Summary

**Date:** 2026-06-29  
**Status:** All local validations passing

---

## Root Causes

### Failure 1: Ruff configuration

- **Error:** `unknown field 'tool'` in `ruff.toml`
- **Cause:** `[tool.ruff]` syntax is valid only in `pyproject.toml`, not standalone `ruff.toml`
- **Fix:** Migrated configuration to `pyproject.toml`; deleted invalid `ruff.toml`
- **Follow-up:** Ran `ruff check --fix` and `ruff format` across the codebase (122 auto-fixes)

### Failure 2: Kubernetes validation

- **Error:** `kubectl apply --dry-run=client` → `dial tcp localhost:8080: connection refused`
- **Cause:** GitHub-hosted runners have no Kubernetes API server; client dry-run attempted OpenAPI schema download
- **Fix:** Replaced cluster-dependent validation with:
  1. `kubectl kustomize` (offline manifest build)
  2. **kubeconform** schema validation (no cluster)
  3. `kubectl apply --dry-run=client --validate=false` (offline syntax check)

---

## Files Modified

| File | Change |
|------|--------|
| `pyproject.toml` | **New** — Ruff, mypy, pytest config |
| `ruff.toml` | **Deleted** — invalid format |
| `pytest.ini` | **Deleted** — merged into pyproject.toml |
| `.github/workflows/main.yml` | Full CI/CD rewrite |
| `proxy.py`, `vllm_patch/*`, `tests/*`, `benchmarks/*` | Ruff auto-format and lint fixes |
| `requirements-dev.txt` | `pip-audit` replaces deprecated `safety` |
| `Makefile` | `pip-audit` instead of `safety` |
| `scripts/validate.ps1` | Offline kubectl + full ruff checks |
| `ci/.github_workflows_main.yml` | Marked deprecated |

---

## CI Pipeline (upgraded)

| Job | Tools |
|-----|-------|
| **lint** | ruff check · ruff format · mypy |
| **test** | pytest matrix 3.10–3.12 · coverage |
| **helm-k8s** | helm lint · helm template · kubeconform · offline dry-run |
| **security** | bandit · pip-audit · trivy fs |
| **docker** | buildx · GHCR push (main/tags) · trivy image · SBOM |
| **release** | helm package · GitHub release on tags |
| **ci-summary** | Aggregate pass/fail gate |

---

## Local Validation Performed

| Command | Result |
|---------|--------|
| `ruff check .` | PASS |
| `ruff format --check .` | PASS |
| `pytest -m "unit or integration or regression"` | 48 passed |
| `helm lint ./helm` | PASS |
| `helm template` | PASS |
| `kubectl kustomize k8s/` | PASS |
| `docker build -f docker/Dockerfile` | PASS |

---

## Notes

- **Black / isort:** Handled by Ruff (`ruff format` is Black-compatible; `I` rules cover isort)
- **GHCR push:** Requires `GITHUB_TOKEN` with `packages: write` — available automatically on GitHub Actions
- **No local infrastructure required** on GitHub-hosted runners
