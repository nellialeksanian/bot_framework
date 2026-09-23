import asyncio

import httpx

from botkit.platform import LLMConfig, LLMGateway, SQLiteUsageTracker
from botkit.runtime import ChatBot
from botkit.transport import IncomingMessage, MemoryAdapter


async def test_same_handler_two_platforms_end_to_end(tmp_path):
    tracker = SQLiteUsageTracker(tmp_path / "usage.db")
    one, two = MemoryAdapter(tracker=tracker, bot_id="one"), MemoryAdapter(tracker=tracker, bot_id="two")
    one.platform, two.platform = "telegram", "vk"
    body = {
        "choices": [{"message": {"content": '{"response":"Готово"}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        llm = LLMGateway(LLMConfig("vllm", "model", "http://test/v1"), client=client, tracker=tracker)
        ChatBot(llm, [one, two])
        await asyncio.gather(
            one.dispatch(IncomingMessage("telegram", "same-id", "c", "text")),
            two.dispatch(IncomingMessage("vk", "same-id", "c", "text")),
        )
        assert one.sent[0]["text"] == two.sent[0]["text"] == "Готово"
        assert (await tracker.stats(platform="vk")).calls == 1
        assert (await tracker.stats(platform="telegram")).calls == 1
        assert (await tracker.stats(bot_id="one", skill_context="CHAT")).output_tokens == 2
        await one.aclose()
        await two.aclose()
