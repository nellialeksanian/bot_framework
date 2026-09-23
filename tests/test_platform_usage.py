import asyncio
import json
import sqlite3
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from botkit.platform import LLMConfig, LLMGateway, PriceBook, SQLiteUsageTracker, UsageEvent, usage_context


def write_prices(path, **models):
    path.write_text(json.dumps({"models": models}), encoding="utf-8")


async def test_decimal_cached_tokens_and_hot_reload(tmp_path):
    path = tmp_path / "prices.json"
    rate = {
        "input_per_million": "2",
        "output_per_million": "5",
        "cached_input_per_million": "0.5",
        "currency": "USD",
    }
    write_prices(path, **{"vllm:m": rate})
    tracker = SQLiteUsageTracker(tmp_path / "usage.db", prices=PriceBook(path))
    event = UsageEvent(
        model="m",
        provider="vllm",
        input_tokens=1000,
        cached_tokens=200,
        output_tokens=100,
        reasoning_tokens=40,
        usage_known=True,
    )
    await tracker.log(event)
    assert (await tracker.stats()).costs_by_currency == {"USD": Decimal("0.0022")}
    write_prices(path, **{"vllm:m": {**rate, "output_per_million": "10", "currency": "RUB"}})
    await tracker.log(replace(event, event_id="second"))
    stats = await tracker.stats()
    assert stats.costs_by_currency == {"USD": Decimal("0.0022"), "RUB": Decimal("0.0027")}
    assert stats.reasoning_tokens == 80  # already included in output, never charged twice


async def test_unknown_model_usage_and_bad_prices(tmp_path):
    path = tmp_path / "prices.json"
    write_prices(path)
    tracker = SQLiteUsageTracker(tmp_path / "usage.db", prices=PriceBook(path))
    await tracker.log(UsageEvent(model="absent", usage_known=True))
    await tracker.log(UsageEvent(model="absent", usage_known=False))
    path.write_text("broken", encoding="utf-8")
    await tracker.log(UsageEvent(model="absent", usage_known=True))
    events = await tracker.events()
    assert [e["cost_status"] for e in events] == ["UNKNOWN_MODEL", "UNKNOWN_USAGE", "PRICE_CONFIG_ERROR"]
    assert all(e["cost"] is None for e in events)


async def test_parallel_context_isolation_and_filters(tmp_path):
    path = tmp_path / "usage.db"
    trackers = [SQLiteUsageTracker(path), SQLiteUsageTracker(path)]

    async def record(i):
        with usage_context(user_id=str(i % 3), skill_context="RACI", bot_id="organizer", platform="vk"):
            await asyncio.sleep(0)
            await trackers[i % 2].log(UsageEvent(model="m", input_tokens=i, usage_known=True))

    await asyncio.gather(*(record(i) for i in range(40)))
    assert (await trackers[0].stats()).calls == 40
    assert (await trackers[0].stats(user_id="0", skill_context="RACI", platform="vk")).calls == 14
    assert (await trackers[0].stats(user_id="0", platform="telegram")).calls == 0
    assert (await trackers[0].stats(bot_id="organizer")).input_tokens == sum(range(40))
    assert len(await trackers[0].events()) == 40


async def test_privacy_append_only_and_export(tmp_path):
    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    event = UsageEvent(
        input_text="private input",
        output_text="private output",
        meta={"api_key": "secret", "nested": {"Authorization": "Bearer abc"}},
    )
    await tracker.log(event)
    with pytest.raises(sqlite3.IntegrityError):
        await tracker.log(event)
    (row,) = await tracker.events()
    assert row["input_text"] == "" and row["output_text"] == ""
    assert row["meta"]["api_key"] == "[REDACTED]"
    assert row["meta"]["nested"]["Authorization"] == "[REDACTED]"
    assert row["meta"]["input_text_chars"] == 13
    path = tmp_path / "export.jsonl"
    assert await tracker.export_jsonl(path) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["event_id"] == event.event_id
    with pytest.raises(FileExistsError):
        await tracker.export_jsonl(path)


async def test_tracker_failure_does_not_break_generation():
    class BrokenTracker:
        async def log(self, event):
            raise OSError("disk unavailable")

    payload = {"choices": [{"message": {"content": "ok"}}]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        gateway = LLMGateway(LLMConfig("vllm", "m", "http://test/v1"), tracker=BrokenTracker(), client=client)
        assert (await gateway.ainvoke("input")).response == "ok"


async def test_explicit_text_logging_limit_and_nested_context(tmp_path):
    tracker = SQLiteUsageTracker(tmp_path / "usage.db", log_text=True, text_limit=3)
    with usage_context(skill_context="outer"):
        with usage_context(skill_context="inner"):
            await tracker.log(UsageEvent(input_text="12345"))
        await tracker.log(UsageEvent())
    rows = await tracker.events()
    assert rows[0]["input_text"] == "123"
    assert [row["skill_context"] for row in rows] == ["inner", "outer"]


async def test_invalid_price_schema_and_numeric_usage_metadata(tmp_path):
    path = tmp_path / "prices.json"
    path.write_text('{"models": []}', encoding="utf-8")
    tracker = SQLiteUsageTracker(tmp_path / "usage.db", prices=PriceBook(path))
    await tracker.log(
        UsageEvent(usage_known=True, meta={"usage": {"prompt_tokens": 12}, "access_token": "secret"})
    )
    (row,) = await tracker.events()
    assert row["cost_status"] == "PRICE_CONFIG_ERROR"
    assert row["meta"]["usage"]["prompt_tokens"] == 12
    assert row["meta"]["access_token"] == "[REDACTED]"
