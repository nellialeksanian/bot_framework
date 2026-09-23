"""Qwen -> hub fallback (-> optional direct 302.ai), including image input."""

import asyncio
import sys
from pathlib import Path

from dotenv import load_dotenv

from botkit.llm import DoNotFallbackError, InvalidLLMResponse, load_llm
from botkit.usage import PriceBook, SQLiteUsageTracker, usage_context


def require_grid(response):
    import json

    try:
        value = json.loads(response.raw)
    except ValueError:
        raise InvalidLLMResponse("Expected grid JSON") from None
    if not isinstance(value, dict) or "grid" not in value:
        raise InvalidLLMResponse("Missing grid field")
    if value["grid"] is None:
        raise DoNotFallbackError("No table: ask the user for a different image")
    if not isinstance(value["grid"], list) or not value["grid"]:
        raise InvalidLLMResponse("Invalid grid")


async def main(path: Path):
    load_dotenv()
    tracker = SQLiteUsageTracker("data/vision.sqlite3", prices=PriceBook("config/prices.json"))
    async with load_llm(tracker=tracker) as llm:
        with usage_context(bot_id="vision-example", skill_context="MATRIX_VISION"):
            result = await llm.ainvoke_with_image(
                'Read the table exactly. Return JSON {"grid": [["cell"]]}; '
                'if no table exists, return {"grid": null}. No markdown.',
                path.read_bytes(),
                mime_type="image/png",
                validator=require_grid,
            )
            print(result.raw)
            print("Answered by:", result.provider, result.model, result.meta.get("fallback_used", False))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))
