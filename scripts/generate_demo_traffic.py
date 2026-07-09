#!/usr/bin/env python3
"""Generate real proxy traffic for Grafana/Prometheus demos."""

from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE = "http://localhost:8082/v1/chat/completions"
PROMPTS = [
    "Explain TTFT in one sentence.",
    "What is token latency?",
    "Count from one to five.",
    "Define inference observability briefly.",
    "How does vLLM batch requests?",
    "What is time between tokens?",
    "Name three GPU metrics.",
    "Summarize Prometheus in ten words.",
    "What makes streaming fast?",
    "Describe a latency histogram.",
    "What is P99 latency?",
    "How do histograms help SLOs?",
    "Explain SSE streaming.",
    "What is end-to-end latency?",
    "Why measure at the proxy?",
]


def one_request(prompt: str, max_tokens: int = 40) -> bool:
    body = json.dumps(
        {
            "model": "facebook/opt-1.3b",
            "stream": True,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
        }
    ).encode()
    req = urllib.request.Request(
        BASE,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        return b"[DONE]" in data
    except Exception as exc:
        print(f"  error: {exc}", file=sys.stderr)
        return False


def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    ok = fail = 0
    t0 = time.time()
    for r in range(1, rounds + 1):
        for i, prompt in enumerate(PROMPTS, 1):
            if one_request(prompt):
                ok += 1
            else:
                fail += 1
            print(f"round {r}/{rounds} req {i}/{len(PROMPTS)} ok={ok} fail={fail}", flush=True)
    elapsed = time.time() - t0
    print(f"DONE ok={ok} fail={fail} elapsed={elapsed:.1f}s rps={ok/elapsed:.2f}")


if __name__ == "__main__":
    main()
