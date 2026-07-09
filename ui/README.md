# Inference Trace — Live Latency Chat UI

A single-file, zero-build chat client for the proxy. It streams responses
token-by-token and renders each reply's **actual per-token timing** as a
trace strip — the amber bar is TTFT, the cyan bars are the gaps between
every token after that. Red bars mark outliers (jitter spikes).

It talks directly to the proxy's OpenAI-compatible endpoint (CORS is already
enabled in `proxy.py`), reads the SSE latency comments the proxy appends
after `data: [DONE]`, and shows the proxy's rolling `/latency/stats` window
in the header ribbon alongside the live connection status from `/health`.

## Run it

No build step — it's one HTML file.

```bash
# with the full stack already up (docker compose / make stack-up)
cd ui
python3 -m http.server 8090
# open http://localhost:8090
```

Or just double-click `index.html` to open it directly in a browser (uses
`fetch`, so this works from `file://` too, as long as the proxy's CORS
headers allow it — which they do by default, `allow_origins=["*"]`).

By default it points at `http://localhost:8080` with model `mock-model`.
Both are editable in the settings row at the top — change them to match
your `PROXY_PORT` and whichever model you passed to vLLM
(`$VLLM_MODEL`/`--model`).

## What the numbers mean

- **TTFT** / **TBT** / **p99 TBT** / **tokens** / **e2e** in each message's
  badge row come straight from the proxy's authoritative, server-side
  measurement (the `x-vllm-*` SSE comments). A `~` after a number means the
  proxy didn't report it and the UI fell back to a client-side timestamp
  estimate (e.g. if you point it at raw vLLM without the proxy in front).
- The header ribbon (`proxy p50/p99 ttft`, `mean tbt`, `window n`) is the
  proxy's own rolling window across **all** traffic it has seen
  (`GET /latency/stats`), not just messages sent from this tab.
- The trace strip's bar heights are capped (600ms for TTFT, 200ms for
  inter-token gaps) so one outlier doesn't flatten the rest of the chart —
  anything past the cap still renders full-height and red.

## Notes

- No backend, no framework, no localStorage — plain JS + `fetch` +
  `ReadableStream`. Safe to serve as a static file from the proxy container,
  nginx, or anywhere else.
- If the status dot is red, it's almost always one of: proxy not running,
  wrong port in the settings row, or CORS blocked by a reverse proxy in
  front of it.
