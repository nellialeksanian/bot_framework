import asyncio
import json
from contextlib import aclosing
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from botkit.llm import (
    DoNotFallbackError,
    FallbackExhaustedError,
    FallbackLLM,
    InvalidLLMResponse,
    LLMConfig,
    LLMGateway,
    LLMResponse,
    ProviderError,
    call_with_fallback,
    load_llm,
    validate_json_response,
)
from botkit.llm.compat import LegacyStringLLM
from botkit.usage import SQLiteUsageTracker, UsageEvent, usage_context


def completed(text='{"response":"ok"}', finish="stop"):
    return {
        "id": "request",
        "choices": [{"message": {"content": text}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 9},
    }


def leaf(client, name, tracker=None):
    return LLMGateway(
        LLMConfig("hub", name, "https://hub.test/ai/v1", "private-key", max_retries=0),
        client=client,
        tracker=tracker,
    )


async def test_failure_then_fallback_accounted_once_per_model(tmp_path):
    bodies = []

    def endpoint(request):
        body = json.loads(request.content)
        bodies.append(body)
        if body["model"] == "qwen":
            return httpx.Response(503)
        return httpx.Response(200, json=completed())

    tracker = SQLiteUsageTracker(tmp_path / "events.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        chain = FallbackLLM(
            leaf(client, "qwen", tracker), [leaf(client, "302-via-hub", tracker)], tracker=tracker
        )
        with usage_context(user_id="u", skill_context="RACI", meta={"chat": "safe"}):
            result = await chain.ainvoke("question", max_tokens=None)
    assert result.response == "ok" and result.meta["fallback_used"]
    assert result.model == "302-via-hub"
    assert "max_tokens" not in bodies[0]
    rows = await tracker.events(kind="llm")
    assert len(rows) == 2 and [r["status"] for r in rows] == ["error", "success"]
    assert rows[0]["request_id"] == rows[1]["request_id"]
    assert rows[1]["meta"]["fallback_from"] == "qwen"
    assert rows[1]["meta"]["fallback_index"] == 1
    assert rows[1]["user_id"] == "u" and rows[1]["skill_context"] == "RACI"
    assert (await tracker.stats()).output_tokens == 9


async def test_vision_payload_identical_through_model_switch():
    bodies = []

    def endpoint(request):
        body = json.loads(request.content)
        bodies.append(body)
        return (
            httpx.Response(503)
            if len(bodies) == 1
            else httpx.Response(200, json=completed('{"grid":[["A"]]}'))
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        chain = FallbackLLM(leaf(client, "qwen"), [leaf(client, "backup")])
        response = await chain.ainvoke_with_image("Read grid", b"image-bytes", mime_type="image/jpeg")
    assert response.meta["fallback_used"]
    assert bodies[0]["messages"] == bodies[1]["messages"]
    assert bodies[0]["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


@pytest.mark.parametrize(
    "primary_text, finish", [("broken-json", "stop"), ("", "stop"), ('{"x":1}', "length")]
)
async def test_invalid_or_truncated_response_falls_back(tmp_path, primary_text, finish):
    tracker = SQLiteUsageTracker(tmp_path / "events.db")

    def endpoint(request):
        first = json.loads(request.content)["model"] == "qwen"
        return httpx.Response(
            200, json=completed(primary_text if first else '{"ok":true}', finish if first else "stop")
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        chain = FallbackLLM(leaf(client, "qwen", tracker), [leaf(client, "backup", tracker)], tracker=tracker)
        response = await chain.ainvoke("json", validator=validate_json_response)
    assert response.meta["fallback_used"]
    assert len(await tracker.events(kind="validation")) == 1
    assert (await tracker.stats()).input_tokens == 22  # even invalid output may have been billed


async def test_domain_result_does_not_trigger_fallback():
    backup = AsyncMock()
    primary = AsyncMock()
    primary.ainvoke.return_value = LLMResponse("", '{"grid":null}', "clean_json")
    primary.model_name = "qwen"

    def validator(response):
        if json.loads(response.raw)["grid"] is None:
            raise DoNotFallbackError("No table on image")

    with pytest.raises(DoNotFallbackError):
        await FallbackLLM(primary, [backup]).ainvoke("read", validator=validator)
    backup.ainvoke.assert_not_called()


async def test_cancel_is_not_fallback():
    entered = asyncio.Event()

    async def hanging(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    primary, backup = AsyncMock(), AsyncMock()
    primary.model_name = "qwen"
    primary.ainvoke.side_effect = hanging
    task = asyncio.create_task(FallbackLLM(primary, [backup]).ainvoke("x"))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    backup.ainvoke.assert_not_called()


async def test_primary_timeout_reserves_time_for_fallback():
    async def hanging(*args, **kwargs):
        await asyncio.Event().wait()

    primary, backup = AsyncMock(), AsyncMock()
    primary.model_name, backup.model_name = "qwen", "backup"
    primary.ainvoke.side_effect = hanging
    backup.ainvoke.return_value = LLMResponse("ok", "ok", "raw_text_fallback")
    response = await FallbackLLM(primary, [backup], primary_timeout=0.02).ainvoke("x", timeout=1)
    assert response.meta["fallback_failures"][0]["error_type"] == "TimeoutError"
    assert response.response == "ok"


async def test_exhaustion_contains_both_failures_but_not_secrets():
    primary, backup = AsyncMock(), AsyncMock()
    primary.model_name, backup.model_name = "qwen", "backup"
    primary.ainvoke.side_effect = ProviderError("sensitive-value", status_code=503)
    backup.ainvoke.side_effect = ProviderError("another-secret", status_code=401)
    with pytest.raises(FallbackExhaustedError) as error:
        await FallbackLLM(primary, [backup]).ainvoke("x")
    assert [e["status_code"] for e in error.value.failures] == [503, 401]
    assert "secret" not in repr(error.value.failures) and "sensitive" not in str(error.value)


async def test_programming_errors_are_not_hidden():
    primary, backup = AsyncMock(), AsyncMock()
    primary.ainvoke.side_effect = TypeError("wrong code")
    with pytest.raises(TypeError):
        await FallbackLLM(primary, [backup]).ainvoke("x")
    backup.ainvoke.assert_not_called()


async def test_old_helper_allows_terminal_domain_exception():
    class NotATableError(ValueError):
        pass

    called = []

    async def domain(client):
        called.append(client)
        raise NotATableError()

    with pytest.raises(NotATableError):
        await call_with_fallback("primary", domain, "backup", no_fallback_for=(NotATableError,))
    assert called == ["primary"]


async def test_raw_bridge_keeps_reflector_phase_metadata():
    payload = '{"response":"Вопрос","layer_complete":true,"layer_data":"Цель"}'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=completed(payload)))
    ) as client:
        adapter = LegacyStringLLM(leaf(client, "m"), raw=True)
        assert json.loads(await adapter.ainvoke("x"))["layer_complete"] is True
        adapter.raw = False
        assert await adapter.ainvoke("x") == "Вопрос"


async def test_factory_organizer_aliases_chain_and_ssl(monkeypatch):
    for key, value in {
        "LLM_PROVIDER": "local_hub",
        "LOCAL_HUB_API_BASE": "https://hub.test/ai/v1",
        "LOCAL_HUB_API_KEY": "secret",
        "LOCAL_HUB_MODEL_NAME": "qwen",
        "LOCAL_HUB_FALLBACK_MODEL_NAME": "gpt-hub",
        "API_302AI_KEY": "direct-secret",
        "A302_MODEL_NAME": "gpt-direct",
        "LLM_DIRECT_FALLBACK": "true",
    }.items():
        monkeypatch.setenv(key, value)
    async with load_llm() as chain:
        assert [p.model_name for p in chain.providers] == ["qwen", "gpt-hub", "gpt-direct"]
        assert chain.providers[0].config.verify_ssl is True
        assert chain.providers[0].config.base_url.endswith("/ai/v1")
        assert chain.providers[2].config.provider == "302ai"
    monkeypatch.setenv("LOCAL_HUB_VERIFY_SSL", "false")
    assert LLMConfig.from_env().verify_ssl is False
    assert LLMConfig.from_env("302ai").verify_ssl is True


@pytest.mark.parametrize("emit_before_error", [False, True])
async def test_stream_switches_only_before_output(emit_before_error):
    calls = []

    def endpoint(request):
        name = json.loads(request.content)["model"]
        calls.append(name)
        if name == "qwen":
            if not emit_before_error:
                return httpx.Response(503)
            return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"part"}}]}\n\n')
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        chain = FallbackLLM(leaf(client, "qwen"), [leaf(client, "backup")])
        async with aclosing(chain.astream([{"role": "user", "content": "x"}])) as stream:
            if emit_before_error:
                with pytest.raises(ProviderError):
                    _ = [c.text async for c in stream]
                assert calls == ["qwen"]
            else:
                assert [c.text async for c in stream] == ["ok"]
                assert calls == ["qwen", "backup"]


def test_existing_positional_contracts():
    timestamp = datetime.now(UTC)
    event = UsageEvent(timestamp, "user", "skill", "model", 10, 20, Decimal("1"), "in", "out", {})
    assert event.timestamp == timestamp and event.input_tokens == 10 and event.model == "model"
    assert LLMResponse("a", "raw", "clean_json", 10, 20).output_tokens == 20


async def test_validator_works_with_a_single_provider_and_keeps_token_usage(tmp_path):
    tracker = SQLiteUsageTracker(tmp_path / "events.db")

    def endpoint(request):
        assert "validator" not in json.loads(request.content)
        return httpx.Response(200, json=completed("not-json"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = leaf(client, "m", tracker)
        with pytest.raises(InvalidLLMResponse):
            await gateway.ainvoke("x", validator=validate_json_response)
    (event,) = await tracker.events()
    assert event["status"] == "error" and event["input_tokens"] == 11
