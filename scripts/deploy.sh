#!/usr/bin/env bash
# Deploy AI Inference Observability Platform (Linux/macOS)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PROXY_PORT="${PROXY_PORT:-8082}"
export PROXY_PORT

echo "=== Building and starting Docker Compose stack ==="
docker compose -f docker/docker-compose.yml up -d --build

echo "=== Waiting for proxy health ==="
for i in $(seq 1 60); do
  if curl -sf "http://localhost:${PROXY_PORT}/health" >/dev/null; then
    echo "Proxy healthy on :${PROXY_PORT}"
    break
  fi
  sleep 5
done

curl -sf "http://localhost:${PROXY_PORT}/health" | python3 -m json.tool

echo "=== Running validation ==="
python3 -m pip install -q -r requirements-dev.txt
python3 -m ruff check .
python3 -m pytest tests/ -m "unit or integration or regression" -q --tb=line

echo "=== Deploy complete ==="
echo "  Proxy:      http://localhost:${PROXY_PORT}"
echo "  Prometheus: http://localhost:9090"
echo "  Grafana:    http://localhost:3000"
