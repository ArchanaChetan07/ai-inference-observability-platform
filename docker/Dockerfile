# =============================================================================
# vLLM Latency Metrics Proxy — Multi-Stage Production Dockerfile
# =============================================================================
# Stage 1: dependency builder (cached layer)
# Stage 2: minimal runtime image
#
# Build:
#   docker build -t vllm-latency-proxy:latest .
#
# Run (development):
#   docker run --rm -p 8080:8080 \
#     -e VLLM_BASE_URL=http://host.docker.internal:8000 \
#     vllm-latency-proxy:latest
#
# Run (with GPU vLLM sidecar via docker-compose):
#   docker-compose up

# -----------------------------------------------------------------------------
# Stage 1 — Builder
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS builder

WORKDIR /app

# Pin apt packages for reproducible builds (regenerate when base image updates).
ARG GCC_VERSION=4:14.2.0-1
ARG GPP_VERSION=4:14.2.0-1
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc=${GCC_VERSION} \
    g++=${GPP_VERSION} \
    && rm -rf /var/lib/apt/lists/*

# Pin pip and install locked Python dependencies (regenerate via pip-compile).
ARG PIP_VERSION=24.3.1
COPY requirements.lock .
RUN pip install --no-cache-dir "pip==${PIP_VERSION}" && \
    pip install --no-cache-dir --prefix=/install -r requirements.lock

# -----------------------------------------------------------------------------
# Stage 2 — Runtime
# -----------------------------------------------------------------------------
FROM python:3.14-slim AS runtime

LABEL org.opencontainers.image.title="vLLM Latency Metrics Proxy"
LABEL org.opencontainers.image.description="Project 1A: TTFT/TBT metrics proxy for vLLM"
LABEL org.opencontainers.image.version="1.2.0"
LABEL org.opencontainers.image.source="https://github.com/ArchanaChetan07/ai-inference-observability-platform"

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Copy application source
COPY proxy.py .
COPY vllm_patch/ ./vllm_patch/

# Create non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Environment defaults (override via docker run -e or docker-compose)
ENV VLLM_BASE_URL=http://vllm:8000
ENV PROXY_HOST=0.0.0.0
ENV PROXY_PORT=8080
ENV LOG_LEVEL=INFO
ENV STATS_WINDOW=1000
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

# Health check — probe our own /health endpoint
HEALTHCHECK \
    --interval=15s \
    --timeout=5s \
    --start-period=10s \
    --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

# Start with uvicorn using uvloop for maximum async performance
CMD ["python", "-m", "uvicorn", "proxy:app", \
     "--host", "0.0.0.0", \
     "--port", "8080", \
     "--workers", "1", \
     "--loop", "uvloop", \
     "--log-level", "info", \
     "--access-log"]
