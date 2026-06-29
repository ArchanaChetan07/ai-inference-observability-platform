"""Concurrent request tests for the latency proxy."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy import ACTIVE_REQUESTS, STATS, app
from tests.helpers import make_non_streaming_response


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_non_streaming_requests():
    """Multiple parallel requests should all receive latency fields."""
    mock_client = AsyncMock()

    async def mock_post(*args, **kwargs):
        mock_resp = MagicMock()
        data = make_non_streaming_response()
        mock_resp.json.return_value = data
        mock_resp.status_code = 200
        mock_resp.content = json.dumps(data).encode()
        await asyncio.sleep(0.01)
        return mock_resp

    mock_client.post = mock_post
    app.state.http_client = mock_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        tasks = [
            client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [], "stream": False},
            )
            for _ in range(20)
        ]
        responses = await asyncio.gather(*tasks)

    assert all(r.status_code == 200 for r in responses)
    assert all("ttft_ms" in r.json()["usage"] for r in responses)
    assert len({r.headers["x-vllm-request-id"] for r in responses}) == 20


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_streaming_requests():
    tokens = ["a", "b", "c"]

    async def mock_aiter_lines():
        for t in tokens:
            chunk = {
                "choices": [{"delta": {"content": t}, "index": 0}],
            }
            yield f"data: {json.dumps(chunk)}"
            await asyncio.sleep(0.002)
        yield "data: [DONE]"

    def make_stream(*args, **kwargs):
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=None)
        ctx.status_code = 200
        ctx.aiter_lines = mock_aiter_lines
        return ctx

    mock_client = AsyncMock()
    mock_client.stream = MagicMock(side_effect=make_stream)
    app.state.http_client = mock_client

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        async def one_stream():
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json={"model": "m", "messages": [], "stream": True},
            ) as resp:
                return (await resp.aread()).decode()

        bodies = await asyncio.gather(*[one_stream() for _ in range(10)])

    assert all("x-vllm-ttft-ms=" in b for b in bodies)
    assert all("data: [DONE]" in b for b in bodies)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_active_requests_gauge_returns_to_zero():
    """ACTIVE_REQUESTS must not leak after concurrent requests complete."""
    mock_client = AsyncMock()
    mock_resp = MagicMock()
    data = make_non_streaming_response()
    mock_resp.json.return_value = data
    mock_resp.status_code = 200
    mock_resp.content = json.dumps(data).encode()

    async def slow_post(*args, **kwargs):
        await asyncio.sleep(0.05)
        return mock_resp

    mock_client.post = slow_post
    app.state.http_client = mock_client

    before = ACTIVE_REQUESTS._value.get()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await asyncio.gather(*[
            client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [], "stream": False},
            )
            for _ in range(5)
        ])

    await asyncio.sleep(0.1)
    after = ACTIVE_REQUESTS._value.get()
    assert after == before
