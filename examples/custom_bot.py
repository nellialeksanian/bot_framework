"""One domain handler, Telegram or VK selected by BOT_PLATFORM. Run from project root."""

import asyncio
import os

from dotenv import load_dotenv

from botkit.platform import PriceBook, SQLiteUsageTracker, load_llm, usage_context
from botkit.runtime import make_adapter
from botkit.transport import Button, IncomingMessage


async def main() -> None:
    load_dotenv()
    tracker = SQLiteUsageTracker("data/custom.sqlite3", prices=PriceBook("config/prices.json"))
    llm = load_llm(tracker=tracker)
    platform = os.getenv("BOT_PLATFORM", "telegram")
    adapter = make_adapter(
        platform, os.environ[f"{platform.upper()}_BOT_TOKEN"], tracker=tracker, bot_id="custom"
    )

    async def start(message: IncomingMessage) -> None:
        await adapter.send(
            message.chat_id,
            "Отправьте текст для разбора.",
            buttons=[[Button("Инструкция", url="https://example.com")]],
        )

    async def review(message: IncomingMessage) -> None:
        # Documents are raw bytes: connect your A5 extractor here if required.
        if not message.text:
            await adapter.send(message.chat_id, "В этом примере нужен текст сообщения.")
            return
        with usage_context(skill_context="ANALYTICAL_REVIEW"):
            async with await adapter.start_typing(message.chat_id):
                result = await llm.ainvoke("Задай уточняющий вопрос по этому тексту:\n" + message.text)
            await adapter.send(message.chat_id, result.response)

    adapter.on_command("start", start)
    adapter.on_message(review)
    try:
        await adapter.run()
    finally:
        await adapter.aclose()
        await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
