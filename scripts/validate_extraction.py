"""Actual supplied fixtures: native PDF/DOCX and optional live scanned-PDF OCR.

Default is local only. --live-provider sends the supplied RACI image as a PDF
page to the selected provider. Keys/URLs/document text are never printed.
"""

import argparse
import asyncio
import json
import logging
import time
from dataclasses import asdict, replace
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from botkit.extraction import ContentExtractionService, ExtractionConfig, render_pdf
from botkit.extraction.documents import DOCX
from botkit.llm import LLMConfig, LLMGateway
from botkit.usage import PriceBook, SQLiteUsageTracker, usage_context


def scanned_pdf(image_path: Path) -> bytes:
    buffer = BytesIO()
    with Image.open(image_path) as image:
        width, height = image.size
    document = canvas.Canvas(buffer, pagesize=(width / 2, height / 2))
    document.drawImage(ImageReader(str(image_path)), 0, 0, width=width / 2, height=height / 2)
    document.showPage()
    document.save()
    return buffer.getvalue()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organizer", type=Path, required=True)
    parser.add_argument("--env", type=Path)
    parser.add_argument("--live-provider", choices=["hub", "302ai"])
    parser.add_argument("--hub-base", help="Explicit hub override supplied by the operator")
    parser.add_argument("--allow-insecure-hub", action="store_true")
    args = parser.parse_args()
    if args.env:
        load_dotenv(args.env, override=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = Path(f"artifacts/extraction-{stamp}")
    output.mkdir(parents=True, exist_ok=False)
    tracker = SQLiteUsageTracker(output / "usage.sqlite3", prices=PriceBook("config/prices.json"))
    config = ExtractionConfig()
    service = ContentExtractionService(config=replace(config, pdf_mode="text"), tracker=tracker)
    report = {"timestamp": stamp, "cases": [], "publication_allowed": False}

    async def check(name, operation):
        started = time.perf_counter()
        with usage_context(bot_id="a5-validation", skill_context=name):
            try:
                details = await operation()
                row = {"name": name, "status": "passed", **details}
            except Exception as exc:
                row = {"name": name, "status": "failed", "error_type": type(exc).__name__}
                cause = exc
                causes = []
                while cause is not None and len(causes) < 5:
                    causes.append(type(cause).__name__)
                    cause = cause.__cause__ or cause.__context__
                row["cause_types"] = causes
            row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
            report["cases"].append(row)
            report["usage"] = asdict(await tracker.stats())
            (output / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
            print(json.dumps(row, ensure_ascii=False), flush=True)

    async def document(path, mime):
        result = await service.extract_bytes(path.read_bytes(), mime, filename=path.name)
        assert result.text and len(result.text) > 100
        return {
            "fixture": path.name,
            "text_chars": len(result.text),
            "warnings": result.warnings,
            "page_count": result.metadata.get("page_count"),
            "table_count": len((result.structured_data or {}).get("tables", [])),
        }

    for path in sorted((args.organizer / "matrices_examples").glob("*.docx")):
        await check("native_docx:" + path.name, lambda path=path: document(path, DOCX))
    for path in sorted(args.organizer.glob("*.pdf")):
        await check("native_pdf", lambda path=path: document(path, "application/pdf"))
    scan = scanned_pdf(args.organizer / "matrices_examples/image.png")

    async def render_case():
        pages = await render_pdf(scan, config=config)
        assert len(pages) == 1
        (output / "rendered-page.png").write_bytes(pages[0].data)
        return {
            "pages": len(pages),
            "width": pages[0].width,
            "height": pages[0].height,
            "rendered_bytes": len(pages[0].data),
        }

    await check("pdf_to_png", render_case)
    if args.live_provider:
        llm_config = replace(LLMConfig.from_env(args.live_provider), timeout=180, max_retries=0, mode="chat")
        if args.live_provider == "hub":
            llm_config = replace(
                llm_config,
                model="qwen3.8-27b-w4a16-awq",
                base_url=args.hub_base or llm_config.base_url,
                verify_ssl=not args.allow_insecure_hub,
                request_interval=2,
                supports_streaming=False,
            )
        async with LLMGateway(llm_config, tracker=tracker) as llm:
            live_service = ContentExtractionService(llm, config=config, tracker=tracker)

            async def vision_case():
                result = await live_service.extract_bytes(scan, "application/pdf")
                assert result.metadata["vision_pages"] == 1 and result.source_confidence == "low"
                tables = result.structured_data["pages"][0]["tables"]
                assert tables and any(len(t["rows"]) >= 14 for t in tables)
                return {
                    "provider": args.live_provider,
                    "vision_pages": 1,
                    "table_count": len(tables),
                    "largest_table_rows": max(len(t["rows"]) for t in tables),
                    "text_chars": len(result.text),
                    "source_confidence": result.source_confidence,
                    "warning_count": len(result.warnings),
                    "content_correctness_asserted": False,
                }

            await check("live_scanned_pdf", vision_case)
    print(f"Report: {output / 'report.json'}", flush=True)
    return int(any(c["status"] != "passed" for c in report["cases"]))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
