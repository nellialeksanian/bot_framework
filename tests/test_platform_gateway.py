import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from botkit.llm.compat import LegacyStringLLM
from botkit.llm.gateway import ProviderError
from botkit.llm.parsing import parse_response
from botkit.platform import LLMConfig, LLMGateway, SQLiteUsageTracker, usage_context


def completion(content='{"response":"Ответ"}', *, usage=True, **extra):
    result = {
        "id": "req-123",
        "model": "reported-model",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        **extra,
    }
    if usage:
        result["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_tokens_details": {"cached_tokens": 30},
            "completion_tokens_details": {"reasoning_tokens": 5},
        }
    return result


@pytest.mark.parametrize(
    ("raw", "expected", "status"),
    [
        ('{"response":"Привет\\n\\"мир\\""}', 'Привет\n"мир"', "clean_json"),
        ('```json\n{"response":"ОК"}\n```', "ОК", "clean_json"),
        ('Вывод: {"response":"Да"} конец', "Да", "regex_fallback"),
        ('"response":"строка\\\\путь"', "строка\\путь", "regex_fallback"),
        ('{"response":null}', '{"response":null}', "raw_text_fallback"),
        ('{"response":"незавершённый', '{"response":"незавершённый', "raw_text_fallback"),
        ("обычный текст", "обычный текст", "raw_text_fallback"),
        ('{"response":""}', "", "clean_json"),
    ],
)
def test_parser(raw, expected, status):
    assert parse_response(raw) == (expected, status)


async def test_chat_protocol_and_usage(tmp_path):
    requests = []

    def endpoint(request):
        requests.append(request)
        return httpx.Response(200, json=completion())

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(
            LLMConfig("vllm", "served-model", "http://localhost:8000/v1", "key"),
            client=client,
            tracker=tracker,
        )
        with usage_context(user_id="u", skill_context="RACI", platform="vk", chat_id="c"):
            result = await gateway.ainvoke(
                "Вопрос", temperature=0, response_format={"type": "json_object"}, extra_body={"top_k": 10}
            )
        assert result.response == "Ответ"
        assert result.cached_tokens == 30 and result.reasoning_tokens == 5
        assert requests[0].url.path == "/v1/chat/completions"
        body = json.loads(requests[0].content)
        assert body["top_k"] == 10 and "extra_body" not in body
        assert requests[0].headers["authorization"] == "Bearer key"
        await gateway.aclose()
        assert not client.is_closed  # injected clients belong to caller
    (event,) = await tracker.events()
    assert (event["user_id"], event["skill_context"], event["chat_id"]) == ("u", "RACI", "c")
    assert event["provider_request_id"] == "req-123"
    assert event["model"] == "served-model"  # rate key remains deployment alias
    assert event["meta"]["reported_model"] == "reported-model"
    assert event["input_text"] == "" and event["cost"] is None


async def test_async_302_pending_then_completion(tmp_path):
    paths = []
    polls = 0

    def endpoint(request):
        nonlocal polls
        paths.append(request.url.path)
        if request.method == "POST":
            assert request.url.params["async"] == "true"
            return httpx.Response(200, json={"task_id": "task-1"})
        polls += 1
        assert request.url.params["task_id"] == "task-1"
        return httpx.Response(
            200,
            json={
                "status_code": 200,
                "err": "pending" if polls == 1 else "",
                "data": None if polls == 1 else json.dumps(completion()),
            },
        )

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(
            LLMConfig("302ai", "m", "https://api.302.ai/v1", "secret", mode="async", poll_interval=0.001),
            client=client,
            tracker=tracker,
        )
        result = await gateway.ainvoke("hello")
    assert result.response == "Ответ" and result.input_tokens == 100
    assert paths == ["/v1/chat/completions", "/v1/async_result", "/v1/async_result"]
    assert (await tracker.events())[0]["poll_count"] == 2


@pytest.mark.parametrize("scenario", ["http", "bad_json", "failed_task", "timeout", "no_choices"])
async def test_failures_are_not_answers(tmp_path, scenario):
    async def endpoint(request):
        if scenario == "http":
            return httpx.Response(401, text="private-secret")
        if scenario == "bad_json":
            return httpx.Response(200, text="not JSON")
        if scenario == "no_choices":
            return httpx.Response(200, json={})
        if request.method == "POST":
            return httpx.Response(200, json={"task_id": "t"})
        if scenario == "timeout":
            return httpx.Response(200, json={"status_code": 200, "err": "pending"})
        return httpx.Response(200, json={"status_code": 500, "err": "private-error"})

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(
            LLMConfig("302ai", "m", "https://api.302.ai/v1", "secret", mode="async", poll_interval=0.001),
            tracker=tracker,
            client=client,
        )
        with pytest.raises((ProviderError, TimeoutError)) as exc:
            await gateway.ainvoke("test", timeout=0.04)
        assert "private" not in str(exc.value)
    stats = await tracker.stats()
    assert stats.calls == stats.errors == 1
    assert stats.unknown_usage_calls == 1


async def test_retry_and_missing_usage(tmp_path):
    calls = 0

    def endpoint(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=completion(usage=False))

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client, tracker=tracker)
        result = await gateway.ainvoke("test")
    assert not result.usage_known
    assert (await tracker.stats()).retries == 1


async def test_cancellation_logged_and_propagated(tmp_path):
    entered = asyncio.Event()

    async def endpoint(request):
        entered.set()
        await asyncio.Event().wait()

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client, tracker=tracker)
        task = asyncio.create_task(gateway.ainvoke("test"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert (await tracker.stats()).cancellations == 1


async def test_tools_vision_and_legacy():
    payloads = []

    def endpoint(request):
        payloads.append(json.loads(request.content))
        if len(payloads) == 1:
            return httpx.Response(
                200,
                json=completion(
                    choices=[
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "t",
                                        "type": "function",
                                        "function": {"name": "check", "arguments": "{}"},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                ),
            )
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client)
        result = await gateway.achat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        assert result.response == "" and result.tool_calls[0]["id"] == "t"
        legacy = LegacyStringLLM(gateway)
        assert await legacy.ainvoke_with_image_b64("Read", "YWJj") == "Ответ"
        assert payloads[1]["messages"][0]["content"][1]["image_url"]["url"] == "data:image/png;base64,YWJj"
        assert await legacy.ainvoke_raw("x") == '{"response":"Ответ"}'


async def test_stream_usage_and_early_close(tmp_path):
    frames = [
        {"id": "s", "choices": [{"delta": {"content": "При"}}]},
        {"id": "s", "choices": [{"delta": {"content": "вет"}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 2, "completion_tokens": 3}},
    ]
    stream = "".join("data: " + json.dumps(f) + "\n\n" for f in frames) + "data: [DONE]\n\n"
    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=stream, headers={"content-type": "text/event-stream"})
        )
    ) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client, tracker=tracker)
        chunks = [c async for c in gateway.astream([{"role": "user", "content": "x"}])]
        assert "".join(c.text for c in chunks) == "Привет"
        async with aclosing(gateway.astream([{"role": "user", "content": "x"}])) as partial:
            async for _ in partial:
                break
    events = await tracker.events()
    assert events[0]["output_tokens"] == 3 and events[0]["first_token_ms"] is not None
    assert events[1]["status"] == "cancelled" and not events[1]["usage_known"]


async def test_embeddings_and_models():
    def endpoint(request):
        if request.url.path.endswith("models"):
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}],
                "usage": {"prompt_tokens": 3},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client)
        assert await gateway.aembed(["one", "two"]) == [[1.0], [2.0]]
        assert await gateway.list_models() == [{"id": "m"}]


def test_env_aliases_and_config_errors(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "local_hub")
    monkeypatch.setenv("LOCAL_HUB_API_BASE", "http://localhost:8000")
    monkeypatch.setenv("LOCAL_HUB_MODEL_NAME", "local-model")
    config = LLMConfig.from_env()
    assert config.provider == "hub" and config.base_url.endswith("/v1")
    with pytest.raises(ValueError):
        LLMConfig("302ai", "m", "https://api.302.ai")
    with pytest.raises(ValueError):
        LLMConfig("vllm", "", "http://localhost")
    with pytest.raises(ValueError):
        LLMConfig("vllm", "m", "http://secret:password@localhost")


async def test_network_post_is_not_replayed_and_stream_truncation_is_error(tmp_path):
    calls = 0

    def endpoint(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("sensitive URL must not leak")
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n')

    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), client=client, tracker=tracker)
        with pytest.raises(ProviderError):
            await gateway.ainvoke("test")
        assert calls == 1
        with pytest.raises(ProviderError, match="before"):
            _ = [chunk async for chunk in gateway.astream([{"role": "user", "content": "test"}])]
    assert (await tracker.stats()).errors == 2
    assert (await tracker.events())[0]["meta"]["network_error"] == "ReadTimeout"
