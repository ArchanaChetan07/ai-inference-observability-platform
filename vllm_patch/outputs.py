"""
vLLM outputs.py patch — Project 1A: Per-Request Latency Metrics
Adds first_token_time, request_start_time, and per-token timestamps
to RequestOutput so TTFT and TBT can be computed and surfaced via API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

# Graceful import — LatencyMetrics is standalone and testable without vLLM installed.
try:
    from vllm.lora.request import LoRARequest
    from vllm.sequence import (PromptLogprobs, RequestMetrics, SampleLogprobs,
                                SequenceGroup, SequenceStatus)
    _VLLM_AVAILABLE = True
except ImportError:
    _VLLM_AVAILABLE = False
    LoRARequest = Any
    PromptLogprobs = Any
    RequestMetrics = Any
    SampleLogprobs = Any
    SequenceGroup = Any
    SequenceStatus = Any


@dataclass
class CompletionOutput:
    """The output data of one completion output of a request."""
    index: int
    text: str
    token_ids: List[int]
    cumulative_logprob: Optional[float]
    logprobs: Optional[Any]
    finish_reason: Optional[str] = None
    stop_reason: Union[int, str, None] = None

    def finished(self) -> bool:
        return self.finish_reason is not None


@dataclass
class LatencyMetrics:
    """
    [PATCH] Per-request latency measurements surfaced to API clients.

    All timestamps use time.monotonic() — meaningful only as deltas.

    Fields
    ------
    request_start_time : float
        Recorded when request is admitted to the AsyncLLMEngine queue.
    first_token_time : Optional[float]
        Recorded when the first output token is generated.
    token_timestamps : List[float]
        Monotonic timestamp per generated token.
    ttft_ms : Optional[float]
        Time-To-First-Token in ms. (first_token_time - request_start_time) * 1000.
    tbt_ms_list : List[float]
        Time-Between-Tokens in ms for each consecutive token pair.
    mean_tbt_ms : Optional[float]
        Mean of tbt_ms_list.
    p99_tbt_ms : Optional[float]
        99th-percentile TBT in ms.
    """

    request_start_time: float = field(default_factory=time.monotonic)
    first_token_time: Optional[float] = None
    token_timestamps: List[float] = field(default_factory=list)

    # Derived — computed lazily via finalize()
    ttft_ms: Optional[float] = None
    tbt_ms_list: List[float] = field(default_factory=list)
    mean_tbt_ms: Optional[float] = None
    p99_tbt_ms: Optional[float] = None

    def record_token(self) -> None:
        """Record a monotonic timestamp for the latest generated token."""
        self.record_token_at(time.monotonic())

    def record_token_at(self, now: float) -> None:
        """Record a token at an explicit monotonic timestamp (for tests)."""
        if self.first_token_time is None:
            self.first_token_time = now
            self.ttft_ms = (now - self.request_start_time) * 1000.0
        self.token_timestamps.append(now)

    def finalize(self) -> None:
        """Compute derived TBT metrics after the last token. Idempotent."""
        if len(self.token_timestamps) < 2:
            return
        from vllm_patch.latency_utils import compute_tbt_ms_list, percentile

        deltas = compute_tbt_ms_list(self.token_timestamps)
        self.tbt_ms_list = deltas
        self.mean_tbt_ms = sum(deltas) / len(deltas)
        self.p99_tbt_ms = percentile(deltas, 0.99)

    def to_dict(self) -> dict:
        """Serialise to a plain dict for JSON responses."""
        return {
            "ttft_ms": round(self.ttft_ms, 3) if self.ttft_ms is not None else None,
            "mean_tbt_ms": round(self.mean_tbt_ms, 3) if self.mean_tbt_ms is not None else None,
            "p99_tbt_ms": round(self.p99_tbt_ms, 3) if self.p99_tbt_ms is not None else None,
            "total_tokens_generated": len(self.token_timestamps),
        }


@dataclass
class RequestOutput:
    """
    The output data of a completion request to the LLM.
    [PATCH] Added `latency` field carrying LatencyMetrics for this request.
    """
    request_id: str
    prompt: Optional[str]
    prompt_token_ids: List[int]
    prompt_logprobs: Optional[Any]
    outputs: List[CompletionOutput]
    finished: bool
    metrics: Optional[Any] = None
    lora_request: Optional[Any] = None

    # [PATCH] latency measurements for this request
    latency: LatencyMetrics = field(default_factory=LatencyMetrics)

    @classmethod
    def from_seq_group(cls, seq_group: Any) -> "RequestOutput":
        if not _VLLM_AVAILABLE:
            raise RuntimeError("vLLM not installed — from_seq_group requires real vLLM")

        seqs = seq_group.get_seqs()
        top_n_seqs = sorted(seqs, key=lambda s: s.get_cumulative_logprob(), reverse=True) \
            if len(seqs) > 1 else seqs

        outputs = [
            CompletionOutput(
                i,
                seq.get_output_text_to_return(seq_group.sampling_params.skip_special_tokens),
                seq.get_output_token_ids(),
                seq.get_cumulative_logprob(),
                seq.output_logprobs if seq_group.sampling_params.logprobs else None,
                SequenceStatus.get_finished_reason(seq.status),
                stop_reason=seq.stop_reason,
            )
            for i, seq in enumerate(top_n_seqs)
        ]

        latency = getattr(seq_group, "_latency_metrics", LatencyMetrics())
        return cls(
            request_id=seq_group.request_id,
            prompt=seq_group.prompt,
            prompt_token_ids=seq_group.prompt_token_ids,
            prompt_logprobs=seq_group.prompt_logprobs,
            outputs=outputs,
            finished=seq_group.is_finished(),
            metrics=seq_group.metrics,
            lora_request=seq_group.lora_request,
            latency=latency,
        )
