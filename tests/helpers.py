"""Shared test helpers."""

from __future__ import annotations

import json


def make_sse_stream(tokens: list[str], model: str = "facebook/opt-1.3b") -> str:
    lines = []
    for i, token in enumerate(tokens):
        chunk = {
            "id": "chatcmpl-test",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None if i < len(tokens) - 1 else "stop",
                }
            ],
        }
        lines.append(f"data: {json.dumps(chunk)}")
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


def make_non_streaming_response(tokens_out: int = 20) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "facebook/opt-1.3b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello there!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": tokens_out,
            "total_tokens": 10 + tokens_out,
        },
    }
