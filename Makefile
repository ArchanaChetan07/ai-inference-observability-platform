# =============================================================================
# vLLM Latency Metrics Proxy — Makefile
# =============================================================================

.PHONY: help install test test-unit test-integration test-e2e test-benchmark \
        lint format typecheck docker-build docker-run stack-up stack-down \
        benchmark clean

VLLM_URL ?= http://localhost:8080
VLLM_MODEL ?= facebook/opt-1.3b
HF_TOKEN ?= $(shell echo $$HF_TOKEN)

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
install:  ## Install all dependencies
	pip install -r requirements-dev.txt

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
test:  ## Run all tests (unit + integration)
	pytest tests/ -m "unit or integration" -v --tb=short

test-unit:  ## Run unit tests only (fast, no network)
	pytest tests/ -m unit -v --tb=short

test-integration:  ## Run integration tests (mocked upstream)
	pytest tests/ -m integration -v --tb=short

test-e2e:  ## Run E2E tests (requires running vLLM at VLLM_E2E_URL)
	VLLM_E2E_URL=$(VLLM_URL) pytest tests/ -m e2e -v --tb=short

test-benchmark:  ## Run benchmark tests
	pytest tests/ -m benchmark -v -s

test-cov:  ## Run tests with coverage report
	pytest tests/ -m "unit or integration" \
		--cov=proxy --cov=vllm_patch \
		--cov-report=html --cov-report=term-missing

# ---------------------------------------------------------------------------
# Code quality
# ---------------------------------------------------------------------------
lint:  ## Run ruff linter
	ruff check proxy.py vllm_patch/ tests/

format:  ## Format code with ruff
	ruff format proxy.py vllm_patch/ tests/

typecheck:  ## Run mypy type checker
	mypy proxy.py vllm_patch/outputs.py --ignore-missing-imports

security:  ## Run security scans
	bandit -r proxy.py vllm_patch/ -ll
	pip-audit -r requirements.txt

# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------
docker-build:  ## Build the proxy Docker image
	docker build -t vllm-latency-proxy:latest -f docker/Dockerfile .

docker-run:  ## Run proxy only (expects vLLM on port 8000)
	docker run --rm -p 8080:8080 \
		-e VLLM_BASE_URL=http://host.docker.internal:8000 \
		vllm-latency-proxy:latest

# ---------------------------------------------------------------------------
# Full stack (docker-compose)
# ---------------------------------------------------------------------------
stack-up:  ## Start the full stack (vLLM + proxy + monitoring)
	@echo "Starting stack with model=$(VLLM_MODEL)..."
	HF_TOKEN=$(HF_TOKEN) VLLM_MODEL=$(VLLM_MODEL) \
		docker compose -f docker/docker-compose.yml up -d
	@echo ""
	@echo "Services:"
	@echo "  vLLM (raw):     http://localhost:8000"
	@echo "  Proxy (metrics): http://localhost:8080"
	@echo "  Grafana:         http://localhost:3000  (admin/admin)"
	@echo "  Prometheus:      http://localhost:9090"

stack-down:  ## Stop the full stack
	docker compose -f docker/docker-compose.yml down

stack-logs:  ## Tail logs from all services
	docker compose -f docker/docker-compose.yml logs -f

# ---------------------------------------------------------------------------
# Quick test against running proxy
# ---------------------------------------------------------------------------
smoke-test:  ## Quick smoke test against $(VLLM_URL)
	@echo "Testing health..."
	curl -sf $(VLLM_URL)/health | python -m json.tool
	@echo ""
	@echo "Testing /latency/stats..."
	curl -sf $(VLLM_URL)/latency/stats | python -m json.tool
	@echo ""
	@echo "Testing non-streaming completion..."
	curl -sf $(VLLM_URL)/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"$(VLLM_MODEL)","messages":[{"role":"user","content":"Say hello in one word."}],"max_tokens":5}' \
		| python -m json.tool
	@echo ""
	@echo "Response headers:"
	curl -si $(VLLM_URL)/v1/chat/completions \
		-H "Content-Type: application/json" \
		-d '{"model":"$(VLLM_MODEL)","messages":[{"role":"user","content":"Hi."}],"max_tokens":5}' \
		| grep -i "x-vllm"

# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------
benchmark:  ## Run full benchmark suite against $(VLLM_URL)
	python benchmarks/run_benchmark.py \
		--base-url $(VLLM_URL) \
		--model $(VLLM_MODEL) \
		--concurrency 1 5 20 \
		--requests-per-level 50

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
clean:  ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	rm -rf .pytest_cache htmlcov .mypy_cache dist build *.egg-info
