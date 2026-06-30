# Kubernetes Troubleshooting

## vLLM pod stuck in Pending

**Symptom:** `kubectl get pods -n vllm` shows vLLM Pending.

**Causes:**
- No GPU nodes or device plugin not installed
- Insufficient GPU/memory on schedulable nodes

**Fix:**
```bash
kubectl describe pod -l app=vllm -n vllm
kubectl get nodes -l nvidia.com/gpu.present=true
```

Install [NVIDIA GPU Operator](https://docs.nvidia.com/datacenter/cloud-native/gpu-operator/latest/getting-started.html) if missing.

## vLLM CrashLoopBackOff / OOM

**Symptom:** vLLM restarts repeatedly.

**Fix:**
- Reduce model size or `VLLM_GPU_MEMORY_UTILIZATION`
- Increase memory limits in deployment
- Use `XFORMERS` attention backend on older GPUs (T4, T1000)

```bash
kubectl logs deployment/vllm -n vllm --tail=100
```

## Proxy pods Ready but 502 from API

**Symptom:** `/health` shows `"upstream": "unreachable"`.

**Fix:**
```bash
kubectl get svc vllm -n vllm
kubectl exec -it deployment/vllm-latency-proxy -n vllm -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://vllm:8000/health').read())"
```

Verify `VLLM_BASE_URL` in ConfigMap matches vLLM service name.

## HPA not scaling

**Symptom:** Replica count stays at minimum.

**Fix:**
```bash
kubectl get hpa -n vllm
kubectl describe hpa vllm-latency-proxy -n vllm
```

Requires metrics-server installed. Proxy is I/O-bound; consider custom metrics on `vllm_proxy_active_requests` for better scaling signal.

## ImagePullBackOff

**Fix:** Build and push proxy image; update `proxy-deployment.yaml` or Helm values:

```bash
docker build -f docker/Dockerfile -t ghcr.io/your-org/vllm-latency-proxy:1.2.0 .
docker push ghcr.io/your-org/vllm-latency-proxy:1.2.0
```

## HF token / model download failures

**Symptom:** vLLM logs show 401 or model not found.

**Fix:**
```bash
kubectl create secret generic hf-token \
  --from-literal=HF_TOKEN=hf_... \
  -n vllm --dry-run=client -o yaml | kubectl apply -f -
kubectl rollout restart deployment/vllm -n vllm
```

## OpenTelemetry traces missing

**Fix:**
- Set `OTEL_ENABLED=true` in ConfigMap
- Verify otel-collector service reachable on port 4317
- Check proxy logs for "OpenTelemetry enabled"

## Rolling update stuck

```bash
kubectl rollout status deployment/vllm-latency-proxy -n vllm
kubectl get events -n vllm --sort-by='.lastTimestamp'
```

If old pods won't terminate, check for long-running SSE connections; adjust `terminationGracePeriodSeconds`.
