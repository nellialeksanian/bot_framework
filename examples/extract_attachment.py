"""Extract one local document through the same A5 service used by bot attachments.

Local text extraction by default. --provider enables billable vision calls.
"""

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from botkit.extraction import ContentExtractionService, ExtractionConfig
from botkit.llm import load_llm
from botkit.transport import Attachment
from botkit.usage import PriceBook, SQLiteUsageTracker, usage_context


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--pdf-mode", choices=["auto", "text", "vision"], default="auto")
    parser.add_argument("--provider", choices=["hub", "302ai", "vllm", "openai_compatible"])
    parser.add_argument("--env", type=Path)
    parser.add_argument("--output", type=Path, help="Write extracted content to a NEW JSON file")
    args = parser.parse_args()
    if args.env:
        load_dotenv(args.env)
    tracker = SQLiteUsageTracker("data/extraction.sqlite3", prices=PriceBook("config/prices.json"))
    llm = load_llm(args.provider, tracker=tracker) if args.provider else None
    config = ExtractionConfig(pdf_mode=args.pdf_mode)
    service = ContentExtractionService(llm, config=config, tracker=tracker)

    async def read():
        return await asyncio.to_thread(args.file.read_bytes)

    attachment = Attachment(
        "document", "application/octet-stream", read, filename=args.file.name, size=args.file.stat().st_size
    )
    try:
        with usage_context(bot_id="extraction-example", skill_context="CONTENT_EXTRACTION"):
            result = await service.extract(attachment)
        if args.output:
            with args.output.open("x", encoding="utf-8") as output:
                json.dump(asdict(result), output, ensure_ascii=False, indent=2)
        print(
            json.dumps(
                {
                    "kind": result.kind,
                    "text_chars": len(result.text or ""),
                    "warnings": result.warnings,
                    "source_confidence": result.source_confidence,
                    "metadata": result.metadata,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if llm:
            await llm.aclose()


if __name__ == "__main__":
    asyncio.run(main())
