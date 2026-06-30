"""
vLLM async_llm_engine.py patch — Project 1A: Per-Request Latency Metrics

This file shows the targeted changes to AsyncLLMEngine that wire up
LatencyMetrics. In your actual vLLM clone, apply these as diffs.

Key change locations are marked with # [PATCH].
"""

from __future__ import annotations

# These imports exist in the real file; shown here for context
# from vllm.outputs import RequestOutput, LatencyMetrics
# from vllm.sequence import SequenceGroup


# ---------------------------------------------------------------------------
# PATCH LOCATION 1: AsyncLLMEngine.add_request()
# File: vllm/engine/async_llm_engine.py
# Method: async def add_request(...)
# ---------------------------------------------------------------------------
PATCH_ADD_REQUEST = """
async def add_request(
    self,
    request_id: str,
    prompt: Optional[str],
    sampling_params: SamplingParams,
    prompt_token_ids: Optional[List[int]] = None,
    arrival_time: Optional[float] = None,
    lora_request: Optional[LoRARequest] = None,
    multi_modal_data: Optional[MultiModalData] = None,
) -> None:
    if arrival_time is None:
        arrival_time = time.monotonic()   # [PATCH] use monotonic for latency math

    # [PATCH] Create a LatencyMetrics object stamped with arrival time
    from vllm.outputs import LatencyMetrics
    latency = LatencyMetrics(request_start_time=arrival_time)

    # Store on the engine's per-request registry so output_processor can find it
    self._latency_registry[request_id] = latency   # [PATCH]

    # ... rest of existing add_request logic unchanged ...
    await self.engine.add_request_async(
        request_id,
        prompt,
        sampling_params,
        prompt_token_ids=prompt_token_ids,
        arrival_time=arrival_time,
        lora_request=lora_request,
        multi_modal_data=multi_modal_data,
    )
"""

# ---------------------------------------------------------------------------
# PATCH LOCATION 2: AsyncLLMEngine.__init__()
# Add the latency registry dict
# ---------------------------------------------------------------------------
PATCH_INIT = """
def __init__(self, ...):
    # ... existing init ...
    self._latency_registry: Dict[str, 'LatencyMetrics'] = {}   # [PATCH]
"""

# ---------------------------------------------------------------------------
# PATCH LOCATION 3: AsyncLLMEngine._run_engine_loop() / output processing
# Every time a RequestOutput is yielded, call latency.record_token()
# ---------------------------------------------------------------------------
PATCH_OUTPUT_PROCESSOR = """
# Inside the loop that yields RequestOutput objects to callers:

for request_output in request_outputs:
    request_id = request_output.request_id

    # [PATCH] Record token timestamp
    latency = self._latency_registry.get(request_id)
    if latency is not None:
        latency.record_token()
        request_output.latency = latency

        # [PATCH] If finished, finalize TBT stats and clean up registry
        if request_output.finished:
            latency.finalize()
            self._latency_registry.pop(request_id, None)

    yield request_output
"""

# ---------------------------------------------------------------------------
# PATCH LOCATION 4: serving_chat.py — inject into HTTP response
# File: vllm/entrypoints/openai/serving_chat.py
# ---------------------------------------------------------------------------
PATCH_SERVING_CHAT = """
# In the streaming response generator, after collecting the final chunk:

# [PATCH] Build latency block for response
latency_dict = {}
if hasattr(final_res, 'latency') and final_res.latency is not None:
    latency_dict = final_res.latency.to_dict()

# [PATCH] Inject into usage object
usage = UsageInfo(
    prompt_tokens=num_prompt_tokens,
    completion_tokens=num_generated_tokens,
    total_tokens=num_prompt_tokens + num_generated_tokens,
    # extended latency fields:
    ttft_ms=latency_dict.get('ttft_ms'),
    mean_tbt_ms=latency_dict.get('mean_tbt_ms'),
    p99_tbt_ms=latency_dict.get('p99_tbt_ms'),
)

# [PATCH] HTTP response headers
response.headers['x-vllm-ttft-ms'] = str(latency_dict.get('ttft_ms', ''))
response.headers['x-vllm-mean-tbt-ms'] = str(latency_dict.get('mean_tbt_ms', ''))
response.headers['x-vllm-p99-tbt-ms'] = str(latency_dict.get('p99_tbt_ms', ''))
response.headers['x-vllm-tokens-generated'] = str(latency_dict.get('total_tokens_generated', ''))
"""

# ---------------------------------------------------------------------------
# PATCH LOCATION 5: protocol.py — extend UsageInfo model
# File: vllm/entrypoints/openai/protocol.py
# ---------------------------------------------------------------------------
PATCH_PROTOCOL = """
class UsageInfo(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0
    completion_tokens: Optional[int] = 0

    # [PATCH] Latency fields — None for non-streaming or if metrics unavailable
    ttft_ms: Optional[float] = Field(
        default=None,
        description="Time-To-First-Token in milliseconds (server-side)."
    )
    mean_tbt_ms: Optional[float] = Field(
        default=None,
        description="Mean Time-Between-Tokens in milliseconds."
    )
    p99_tbt_ms: Optional[float] = Field(
        default=None,
        description="P99 Time-Between-Tokens in milliseconds."
    )
"""


def demonstrate_patch_application():
    """
    Shows what the combined patches achieve end-to-end.
    This function is documentation — not executed in production.
    """
    print("Patch flow:")
    print("1. Request arrives → add_request() stamps LatencyMetrics(start=now)")
    print("2. Each token generated → record_token() appends timestamp")
    print("3. First token → ttft_ms computed automatically")
    print("4. Request finishes → finalize() computes mean/p99 TBT")
    print("5. serving_chat.py reads latency.to_dict()")
    print("6. Response headers: x-vllm-ttft-ms, x-vllm-mean-tbt-ms, ...")
    print("7. Response body usage: {ttft_ms, mean_tbt_ms, p99_tbt_ms}")
