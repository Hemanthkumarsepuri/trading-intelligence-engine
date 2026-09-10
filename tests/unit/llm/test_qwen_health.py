from __future__ import annotations

import httpx
import pytest

from app.llm.qwen_narrative import QwenNarrativeAdapter


@pytest.mark.asyncio
async def test_health_probe_uses_models_endpoint_not_generation() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder-7b-instruct"}]})
        raise AssertionError("health must not call chat completions")

    adapter = QwenNarrativeAdapter(enabled=True, health_timeout_seconds=0.5)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adapter.connectivity_probe(client)
    assert result.ok is True
    assert result.status == "QWEN_READY"
    assert seen == ["GET /v1/models"] or all("/chat/completions" not in item for item in seen)


@pytest.mark.asyncio
async def test_health_probe_timeout_is_bounded() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    adapter = QwenNarrativeAdapter(enabled=True, health_timeout_seconds=0.01)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adapter.connectivity_probe(client)
    assert result.ok is False
    assert result.status == "QWEN_TIMEOUT"


@pytest.mark.asyncio
async def test_health_probe_not_configured() -> None:
    adapter = QwenNarrativeAdapter(enabled=False)
    async with httpx.AsyncClient() as client:
        result = await adapter.connectivity_probe(client)
    assert result.status == "QWEN_NOT_CONFIGURED"
