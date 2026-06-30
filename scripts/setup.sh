#!/usr/bin/env bash
# =============================================================================
# setup.sh — One-command setup for T1000 + WSL2
# =============================================================================
# Run this once to verify your environment and start the stack.
# Usage: bash scripts/setup.sh

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "${BLUE}[setup]${NC} $1"; }
ok()  { echo -e "${GREEN}[  OK ]${NC} $1"; }
warn(){ echo -e "${YELLOW}[ WARN]${NC} $1"; }
fail(){ echo -e "${RED}[FAIL ]${NC} $1"; exit 1; }

echo ""
echo "========================================="
echo " vLLM Latency Metrics — Environment Check"
echo "========================================="
echo ""

# ---------------------------------------------------------------------------
# 1. NVIDIA Driver
# ---------------------------------------------------------------------------
log "Checking NVIDIA driver..."
if ! command -v nvidia-smi &>/dev/null; then
    fail "nvidia-smi not found. Install NVIDIA drivers in Windows, then enable GPU in WSL2."
fi
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -1)
GPU=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)
VRAM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1)
ok "GPU: $GPU | VRAM: $VRAM | Driver: $DRIVER"

# ---------------------------------------------------------------------------
# 2. Docker
# ---------------------------------------------------------------------------
log "Checking Docker..."
if ! command -v docker &>/dev/null; then
    fail "Docker not found. Install Docker Desktop with WSL2 backend."
fi
DOCKER_VER=$(docker --version | grep -oP '[\d.]+' | head -1)
ok "Docker $DOCKER_VER"

# ---------------------------------------------------------------------------
# 3. NVIDIA Container Runtime
# ---------------------------------------------------------------------------
log "Checking NVIDIA Container Runtime..."
if ! docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi &>/dev/null; then
    warn "GPU not accessible in Docker. Installing NVIDIA Container Toolkit..."
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
        sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
        sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
        sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update -qq
    sudo apt-get install -y nvidia-container-toolkit
    sudo nvidia-ctk runtime configure --runtime=docker
    sudo systemctl restart docker
    ok "NVIDIA Container Toolkit installed"
else
    ok "GPU accessible in Docker containers"
fi

# ---------------------------------------------------------------------------
# 4. Python
# ---------------------------------------------------------------------------
log "Checking Python..."
PYTHON=$(python3 --version 2>/dev/null || echo "not found")
if [[ "$PYTHON" == "not found" ]]; then
    fail "Python 3 not found. Run: sudo apt install python3.11"
fi
ok "$PYTHON"

# ---------------------------------------------------------------------------
# 5. HuggingFace token
# ---------------------------------------------------------------------------
log "Checking HuggingFace token..."
if [[ -z "${HF_TOKEN:-}" ]]; then
    warn "HF_TOKEN not set. The model download will fail without it."
    echo "  Get a token from: https://huggingface.co/settings/tokens"
    echo "  Then: export HF_TOKEN=hf_..."
    echo "  Or add to ~/.bashrc: echo 'export HF_TOKEN=hf_...' >> ~/.bashrc"
else
    ok "HF_TOKEN is set (${#HF_TOKEN} chars)"
fi

# ---------------------------------------------------------------------------
# 6. Install Python deps
# ---------------------------------------------------------------------------
log "Installing Python dependencies..."
pip install -r requirements-dev.txt -q
ok "Python dependencies installed"

# ---------------------------------------------------------------------------
# 7. Run unit tests
# ---------------------------------------------------------------------------
log "Running unit tests..."
if pytest tests/ -m unit -q --tb=short 2>&1 | tail -5; then
    ok "Unit tests passed"
else
    fail "Unit tests failed — fix before proceeding"
fi

# ---------------------------------------------------------------------------
# 8. Print startup instructions
# ---------------------------------------------------------------------------
echo ""
echo "========================================="
echo " All checks passed! Next steps:"
echo "========================================="
echo ""
echo "  Option A — Full stack with monitoring:"
echo "    make stack-up HF_TOKEN=\$HF_TOKEN"
echo ""
echo "  Option B — Proxy only (assumes vLLM already running):"
echo "    make docker-build"
echo "    make docker-run"
echo ""
echo "  Access points (once stack is up):"
echo "    Proxy (with metrics): http://localhost:8080"
echo "    Grafana:              http://localhost:3000  (admin/admin)"
echo "    Prometheus:           http://localhost:9090"
echo ""
echo "  Quick smoke test:"
echo "    make smoke-test"
echo ""
echo "  Full benchmark:"
echo "    make benchmark"
echo ""
