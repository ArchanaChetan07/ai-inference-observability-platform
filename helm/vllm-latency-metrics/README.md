# DEPRECATED

This nested chart is **deprecated**. Use the root chart at [`../`](../) instead.

```bash
helm upgrade --install latency-metrics ../helm -n vllm --create-namespace \
  -f ../helm/values-prod.yaml
```

The root chart includes all features: Alertmanager, Ingress, NetworkPolicy, OTel, and prod/dev overlays.
