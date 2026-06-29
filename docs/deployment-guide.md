# Production Deployment Guide

## Deployment Options

| Method | Use case |
|--------|----------|
| Docker Compose | Local dev, CI, single-GPU workstation |
| Kubernetes (kubectl) | Production clusters, GitOps |
| Helm | Parameterized installs, multi-environment |

## Quick Start — Docker Compose

```bash
export HF_TOKEN=hf_...
docker compose -f docker/docker-compose.yml up -d --build
curl http://localhost:8080/health
```

## Quick Start — Helm (recommended for K8s)

```bash
kubectl create namespace vllm
kubectl create secret generic hf-token --from-literal=HF_TOKEN=hf_... -n vllm

helm install latency-metrics ./helm \
  -n vllm \
  --set vllm.model=facebook/opt-1.3b \
  --set proxy.image.repository=ghcr.io/your-username/vllm-latency-proxy \
  --set proxy.image.tag=1.2.0

helm status latency-metrics -n vllm
```

With monitoring and tracing:

```bash
helm install latency-metrics ./helm -n vllm \
  --set prometheus.enabled=true \
  --set grafana.enabled=true \
  --set opentelemetry.enabled=true \
  --set opentelemetry.collector.enabled=true
```

## Quick Start — kubectl + Kustomize

```bash
kubectl create secret generic hf-token --from-literal=HF_TOKEN=hf_... -n vllm
kubectl apply -k k8s/
```

## Production Examples

### Small GPU node (8 GB, T4/T1000)

```yaml
vllm:
  model: facebook/opt-1.3b
  attentionBackend: XFORMERS
  gpuMemoryUtilization: "0.85"
proxy:
  replicaCount: 2
```

### A100 80GB — larger model

```yaml
vllm:
  model: meta-llama/Llama-3.1-8B-Instruct
  maxModelLen: 8192
  gpuMemoryUtilization: "0.90"
  gpuCount: 1
```

### Multi-GPU tensor parallel (Helm extraEnv + custom args)

Extend `vllm-deployment.yaml` or use `--set vllm.gpuCount=4` with TP startup script for 70B-class models.

## Upgrade

```bash
helm upgrade latency-metrics ./helm -n vllm --set proxy.image.tag=1.3.0
kubectl rollout status deployment/latency-metrics-vllm-latency-metrics-proxy -n vllm
```

## Rollback

```bash
helm rollback latency-metrics -n vllm
```
