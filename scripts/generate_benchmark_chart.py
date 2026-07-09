"""Regenerate README benchmark charts from live results in benchmarks/results/.

Run from the repo root:

    python scripts/generate_benchmark_chart.py

Outputs:
  docs/images/benchmark-overhead.png
  docs/images/benchmark-ttft-percentiles.png
  docs/images/benchmark-ttft-distribution.png
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

DIRECT_JSON = Path("benchmarks/results/benchmark_20260709_122449.json")
PROXY_JSON = Path("benchmarks/results/benchmark_20260709_121220.json")
OUT_DIR = Path("docs/images")

STYLE = {
    "font.family": "monospace",
    "axes.edgecolor": "#4d5567",
    "axes.labelcolor": "#1a1d24",
    "text.color": "#1a1d24",
    "xtick.color": "#1a1d24",
    "ytick.color": "#1a1d24",
}
COLORS = {
    "direct": "#9aa2b1",
    "proxy": "#ffb454",
    "tbt": "#4fd8e0",
    "e2e": "#7c8494",
}
SUBTITLE = (
    "facebook/opt-1.3b · NVIDIA T1000 8GB · 100 max tokens · streaming · 50 req/level\n"
    "source: benchmark_20260709_122449.json (direct) · _121220.json (proxy)"
)


def load_summaries() -> tuple[list[dict], list[dict]]:
    direct = json.loads(DIRECT_JSON.read_text())["summaries"]
    proxy = json.loads(PROXY_JSON.read_text())["summaries"]
    return direct, proxy


def load_raw_ttft(proxy_path: Path, concurrency: int = 1) -> list[float]:
    raw = json.loads(proxy_path.read_text())["raw"]
    return [r["ttft_ms"] for r in raw if r["concurrency"] == concurrency and r["ttft_ms"] is not None]


def chart_overhead(direct: list[dict], proxy: list[dict]) -> None:
    concurrencies = [s["concurrency"] for s in direct]
    x = np.arange(len(concurrencies))
    w = 0.32

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9.5, 3.8), dpi=150)
    fig.patch.set_facecolor("white")

    for ax, direct_vals, proxy_vals, ylabel, title in (
        (
            ax1,
            [s["ttft_p99"] for s in direct],
            [s["ttft_p99"] for s in proxy],
            "TTFT P99 (ms)",
            "TTFT P99: direct vs proxy",
        ),
        (
            ax2,
            [s["throughput_rps"] for s in direct],
            [s["throughput_rps"] for s in proxy],
            "Throughput (req/s)",
            "Throughput: direct vs proxy",
        ),
    ):
        b1 = ax.bar(x - w / 2, direct_vals, width=w, label="vLLM direct", color=COLORS["direct"])
        b2 = ax.bar(x + w / 2, proxy_vals, width=w, label="Via proxy", color=COLORS["proxy"])
        ax.set_xticks(x)
        ax.set_xticklabels([f"c={c}" for c in concurrencies])
        ax.set_ylabel(ylabel)
        ax.set_title(title, fontsize=10, fontweight="bold")
        for bars, fmt in ((b1, "{:.0f}"), (b2, "{:.0f}")) if "TTFT" in title else ((b1, "{:.2f}"), (b2, "{:.2f}")):
            for bar in bars:
                h = bar.get_height()
                label = fmt.format(h)
                ax.annotate(label, (bar.get_x() + bar.get_width() / 2, h), ha="center", va="bottom", fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(fontsize=8, frameon=False)

    fig.suptitle(SUBTITLE, fontsize=7.5, y=1.04, color="#4d5567")
    fig.tight_layout()
    fig.savefig(OUT_DIR / "benchmark-overhead.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def chart_percentiles(direct: list[dict], proxy: list[dict]) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(9.5, 3.8), dpi=150, sharey=True)
    fig.patch.set_facecolor("white")

    percentiles = ("p50", "p95", "p99")
    labels = ("P50", "P95", "P99")
    keys = ("ttft_p50", "ttft_p95", "ttft_p99")

    for ax, summary, title, color in (
        (axes[0], direct[0], "vLLM direct · concurrency 1", COLORS["direct"]),
        (axes[1], proxy[0], "Via proxy · concurrency 1", COLORS["proxy"]),
    ):
        vals = [summary[k] for k in keys]
        bars = ax.bar(labels, vals, color=color, width=0.55)
        ax.set_ylabel("TTFT (ms)")
        ax.set_title(title, fontsize=10, fontweight="bold")
        ax.set_ylim(0, max(vals) * 1.2)
        for bar in bars:
            h = bar.get_height()
            ax.annotate(f"{h:.0f}", (bar.get_x() + bar.get_width() / 2, h), ha="center", va="bottom", fontsize=8)
        ax.spines[["top", "right"]].set_visible(False)

    fig.suptitle("TTFT percentile spread @ concurrency 1", fontsize=10, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(OUT_DIR / "benchmark-ttft-percentiles.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def chart_distribution(ttft_ms: list[float]) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=150)
    fig.patch.set_facecolor("white")

    ax.hist(ttft_ms, bins=12, color=COLORS["proxy"], edgecolor="white", alpha=0.9)
    ax.axvline(np.percentile(ttft_ms, 50), color="#1a1d24", linestyle="--", linewidth=1.2, label=f"P50 {np.percentile(ttft_ms, 50):.0f} ms")
    ax.axvline(np.percentile(ttft_ms, 99), color="#e5484d", linestyle="--", linewidth=1.2, label=f"P99 {np.percentile(ttft_ms, 99):.0f} ms")
    ax.set_xlabel("TTFT (ms)")
    ax.set_ylabel("Requests")
    ax.set_title("Proxy TTFT distribution · concurrency 1 · n=50", fontsize=10, fontweight="bold")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(fontsize=8, frameon=False)

    fig.tight_layout()
    fig.savefig(OUT_DIR / "benchmark-ttft-distribution.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def main() -> None:
    plt.rcParams.update(STYLE)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    direct, proxy = load_summaries()
    ttft_samples = load_raw_ttft(PROXY_JSON, concurrency=1)

    chart_overhead(direct, proxy)
    chart_percentiles(direct, proxy)
    chart_distribution(ttft_samples)

    print("saved:")
    for name in ("benchmark-overhead.png", "benchmark-ttft-percentiles.png", "benchmark-ttft-distribution.png"):
        print(f"  docs/images/{name}")


if __name__ == "__main__":
    main()
