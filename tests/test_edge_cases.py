"""Edge-case and regression tests for the latency proxy."""

from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy import app
from tests.helpers import make_non_streaming_response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_malformed_json_body_passthrough_as_empty():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"usage": {}, "choices": []}
    mock_resp.status_code = 200
    mock_resp.content = b"{}"
    mock_client.post = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
    assert resp.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_upstream_error_status_preserved():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.json.side_effect = ValueError("not json")
    mock_resp.status_code = 503
    mock_resp.content = b"Service Unavailable"
    mock_client.post = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [], "stream": False},
        )
    assert resp.status_code == 503


@pytest.mark.integration
@pytest.mark.asyncio
async def test_streaming_no_content_tokens_still_completes():
    """Stream with only role deltas should not crash; TTFT falls back to E2E."""
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"}}]}',
        "data: [DONE]",
    ]
    sse = "\n".join(lines) + "\n"

    async def mock_aiter_lines():
        for line in sse.split("\n"):
            if line:
                yield line

    mock_client = AsyncMock()
    mock_context = AsyncMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_context)
    mock_context.__aexit__ = AsyncMock(return_value=None)
    mock_context.status_code = 200
    mock_context.aiter_lines = mock_aiter_lines
    mock_client.stream = MagicMock(return_value=mock_context)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            json={"model": "m", "messages": [], "stream": True},
        ) as resp:
            body = (await resp.aread()).decode()

    assert "data: [DONE]" in body
    assert "x-vllm-ttft-ms=" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_completions_endpoint_instrumented():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    data = make_non_streaming_response()
    mock_resp.json.return_value = data
    mock_resp.status_code = 200
    mock_resp.content = json.dumps(data).encode()
    mock_client.post = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/completions",
            json={"model": "m", "prompt": "hi", "stream": False},
        )
    assert resp.status_code == 200
    assert "ttft_ms" in resp.json()["usage"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_custom_request_id_propagated():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    data = make_non_streaming_response()
    mock_resp.json.return_value = data
    mock_resp.status_code = 200
    mock_resp.content = json.dumps(data).encode()
    mock_client.post = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [], "stream": False},
            headers={"x-request-id": "custom-req-42"},
        )
    assert resp.headers["x-vllm-request-id"] == "custom-req-42"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_passthrough_models_endpoint():
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.content = b'{"object":"list","data":[]}'
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_client.request = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/models")
    assert resp.status_code == 200
    assert resp.json()["object"] == "list"


@pytest.mark.regression
@pytest.mark.integration
@pytest.mark.asyncio
async def test_openai_usage_token_fields_unchanged():
    """Regression: standard usage fields must remain intact."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    data = make_non_streaming_response(tokens_out=42)
    mock_resp.json.return_value = data
    mock_resp.status_code = 200
    mock_resp.content = json.dumps(data).encode()
    mock_client.post = AsyncMock(return_value=mock_resp)
    app.state.http_client = mock_client

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "m", "messages": [], "stream": False},
        )
    usage = resp.json()["usage"]
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 42
    assert usage["total_tokens"] == 52
