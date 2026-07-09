"""Regenerate docs/images/benchmark-overhead.png from the real benchmark
results in benchmarks/results/. Run from the repo root:

    python scripts/generate_benchmark_chart.py
"""

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

direct = json.load(open("benchmarks/results/benchmark_20260629_110725.json"))["summaries"]
proxy = json.load(open("benchmarks/results/benchmark_20260629_110852.json"))["summaries"]

concurrencies = [s["concurrency"] for s in direct]
direct_ttft = [s["ttft_p99"] for s in direct]
proxy_ttft = [s["ttft_p99"] for s in proxy]
direct_rps = [s["throughput_rps"] for s in direct]
proxy_rps = [s["throughput_rps"] for s in proxy]

plt.rcParams.update(
    {
        "font.family": "monospace",
        "axes.edgecolor": "#4d5567",
        "axes.labelcolor": "#1a1d24",
        "text.color": "#1a1d24",
        "xtick.color": "#1a1d24",
        "ytick.color": "#1a1d24",
    }
)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 3.6), dpi=150)
fig.patch.set_facecolor("white")

x = range(len(concurrencies))
w = 0.32

# Panel 1: TTFT P99
b1 = ax1.bar([i - w / 2 for i in x], direct_ttft, width=w, label="vLLM direct", color="#9aa2b1")
b2 = ax1.bar([i + w / 2 for i in x], proxy_ttft, width=w, label="Via proxy", color="#ffb454")
ax1.set_xticks(list(x))
ax1.set_xticklabels([f"c={c}" for c in concurrencies])
ax1.set_ylabel("TTFT P99 (ms)")
ax1.set_title("TTFT P99: direct vs proxy", fontsize=10, fontweight="bold")
for bars in (b1, b2):
    for bar in bars:
        h = bar.get_height()
        ax1.annotate(
            f"{h:.0f}", (bar.get_x() + bar.get_width() / 2, h), ha="center", va="bottom", fontsize=8
        )
ax1.spines[["top", "right"]].set_visible(False)
ax1.legend(fontsize=8, frameon=False)

# Panel 2: throughput RPS
b3 = ax2.bar([i - w / 2 for i in x], direct_rps, width=w, label="vLLM direct", color="#9aa2b1")
b4 = ax2.bar([i + w / 2 for i in x], proxy_rps, width=w, label="Via proxy", color="#4fd8e0")
ax2.set_xticks(list(x))
ax2.set_xticklabels([f"c={c}" for c in concurrencies])
ax2.set_ylabel("Throughput (req/s)")
ax2.set_title("Throughput: direct vs proxy", fontsize=10, fontweight="bold")
for bars in (b3, b4):
    for bar in bars:
        h = bar.get_height()
        ax2.annotate(
            f"{h:.2f}", (bar.get_x() + bar.get_width() / 2, h), ha="center", va="bottom", fontsize=8
        )
ax2.spines[["top", "right"]].set_visible(False)
ax2.legend(fontsize=8, frameon=False)

fig.suptitle(
    "facebook/opt-1.3b · NVIDIA T1000 8GB · 30 tokens/request · streaming\n"
    "source: benchmarks/results/benchmark_20260629_110725.json (direct), _110852.json (proxy)",
    fontsize=7.5,
    y=1.03,
    color="#4d5567",
)
fig.tight_layout()
fig.savefig("docs/images/benchmark-overhead.png", bbox_inches="tight", facecolor="white")
print("saved")
