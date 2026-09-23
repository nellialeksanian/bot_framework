import asyncio
import json
import time
from contextlib import aclosing

import httpx
import pytest

from botkit.llm import LLMConfig, LLMGateway


async def test_hub_defaults_non_streaming_and_two_second_gap(monkeypatch):
    monkeypatch.setenv("LOCAL_HUB_API_BASE", "https://hub.test/v1")
    monkeypatch.setenv("LOCAL_HUB_MODEL_NAME", "qwen3.8-27b-w4a16-awq")
    config = LLMConfig.from_env("hub")
    assert config.request_interval == 2 and config.supports_streaming is False


async def test_pacing_shared_between_models_and_waits_after_completion():
    starts, finishes = [], []

    async def endpoint(request):
        assert json.loads(request.content)["stream"] is False
        starts.append(time.perf_counter())
        await asyncio.sleep(0.03)
        finishes.append(time.perf_counter())
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        providers = [
            LLMGateway(LLMConfig("hub", name, "https://pace.test/v1", request_interval=0.04), client=client)
            for name in ("primary", "backup")
        ]
        await asyncio.gather(*(p.ainvoke("hi") for p in providers))
    assert starts[1] - finishes[0] >= 0.035


async def test_streaming_disabled_before_network():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("No network expected"))
    ) as client:
        gateway = LLMGateway(
            LLMConfig("hub", "qwen", "https://pace.test/v1", supports_streaming=False), client=client
        )
        with pytest.raises(ValueError, match="disabled"):
            async with aclosing(gateway.astream([{"role": "user", "content": "hi"}])) as stream:
                _ = [item async for item in stream]


async def test_cancelled_queued_request_does_not_deadlock_following_requests():
    async def endpoint(request):
        await asyncio.sleep(0.02)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(
            LLMConfig("hub", "qwen", "https://cancel.test/v1", request_interval=0.03), client=client
        )
        first = asyncio.create_task(gateway.ainvoke("first"))
        await asyncio.sleep(0.005)
        queued = asyncio.create_task(gateway.ainvoke("cancel"))
        await asyncio.sleep(0.005)
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        await first
        result = await gateway.ainvoke("next", timeout=1)
        assert result.response == "OK"
