from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import PurePath

from botkit.llm import LLMProvider
from botkit.llm.base import AudioTranscriptionProvider
from botkit.transport.base import Attachment
from botkit.usage import UsageEvent, UsageTracker, current_context, safe_log, usage_context

from .audio import AudioExtractor
from .base import ExtractedContent, Extractor
from .config import ExtractionConfig, ExtractionError, UnsupportedContentError
from .documents import DOCX, PPTX, XLSX, OfficeExtractor, TextExtractor, inspect_archive
from .pdf import PDFExtractor
from .vision import ImageExtractor

EXTENSIONS = {
    ".pdf": "application/pdf",
    ".docx": DOCX,
    ".xlsx": XLSX,
    ".pptx": PPTX,
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".html": "text/html",
    ".htm": "text/html",
    ".xml": "application/xml",
}


def detect_mime(data: bytes, declared: str, filename: str | None, config: ExtractionConfig) -> str:
    declared = declared.split(";", 1)[0].strip().lower()
    if data.lstrip().startswith(b"%PDF-"):
        return "application/pdf"
    signatures = [
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"\xff\xd8\xff", "image/jpeg"),
        (b"GIF8", "image/gif"),
        (b"II*\x00", "image/tiff"),
        (b"MM\x00*", "image/tiff"),
        (b"BM", "image/bmp"),
    ]
    for signature, mime in signatures:
        if data.startswith(signature):
            return mime
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"PK\x03\x04"):
        names = inspect_archive(data, config)
        for name, mime in (
            ("word/document.xml", DOCX),
            ("xl/workbook.xml", XLSX),
            ("ppt/presentation.xml", PPTX),
        ):
            if name in names:
                return mime
        raise UnsupportedContentError("ZIP archives are not supported; send individual documents")
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise UnsupportedContentError("Legacy or encrypted Office document; export as PDF/DOCX/XLSX/PPTX")
    if declared in {"", "application/octet-stream", "binary/octet-stream"}:
        return EXTENSIONS.get(PurePath(filename or "").suffix.lower(), declared)
    return {"image/jpg": "image/jpeg", "application/x-pdf": "application/pdf"}.get(declared, declared)


class ContentExtractionService:
    """Concrete A5 service; custom registered extractors take precedence.

    The supplied LLM/tracker belong to the caller. No private client is created.
    """

    def __init__(
        self,
        llm: LLMProvider | None = None,
        *,
        config: ExtractionConfig | None = None,
        tracker: UsageTracker | None = None,
        transcriber: AudioTranscriptionProvider | None = None,
    ):
        self.config = config or ExtractionConfig()
        self.tracker = tracker
        self.vision = ImageExtractor(llm, self.config) if llm is not None else None
        self._extractors: list[Extractor] = [
            PDFExtractor(self.vision, self.config),
            OfficeExtractor(self.config),
            TextExtractor(self.config),
        ]
        if self.vision is not None:
            self._extractors.append(self.vision)
        if transcriber is not None:
            self._extractors.append(AudioExtractor(transcriber, self.config))

    def register(self, extractor: Extractor) -> None:
        self._extractors.insert(0, extractor)

    async def extract(self, attachment: Attachment) -> ExtractedContent:
        if attachment.size is not None and attachment.size > self.config.max_bytes:
            from .config import ExtractionLimitError

            raise ExtractionLimitError("Attachment exceeds extraction size limit")
        data = await attachment.read_bytes()
        return await self.extract_bytes(data, attachment.mime_type, filename=attachment.filename)

    async def extract_bytes(
        self, data: bytes, mime_type: str, *, filename: str | None = None
    ) -> ExtractedContent:
        started = time.perf_counter()
        event = UsageEvent(kind="extraction", operation="extract", cost_status="NOT_APPLICABLE")
        try:
            self.config.check_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            detected = await asyncio.to_thread(detect_mime, data, mime_type, filename, self.config)
            event.meta.update(mime_type=detected, byte_count=len(data), source_sha256=digest)
            context = current_context()
            with usage_context(
                meta={
                    **context.get("meta", {}),
                    "extraction_sha256": digest,
                    "extraction_mime_type": detected,
                }
            ):
                for extractor in self._extractors:
                    if await extractor.supports(detected):
                        result = await extractor.extract(data, detected)
                        if result.text is not None:
                            self.config.check_text(result.text)
                        result.metadata.update(mime_type=detected, byte_count=len(data), source_sha256=digest)
                        if detected != mime_type.split(";", 1)[0].strip().lower():
                            result.warnings.append("mime_type_inferred_from_content_or_filename")
                        event.meta.update(
                            warning_count=len(result.warnings),
                            source_confidence=result.source_confidence,
                            text_chars=len(result.text or ""),
                            page_count=result.metadata.get("page_count"),
                        )
                        return result
            if detected.startswith("image/") and self.vision is None:
                raise ExtractionError("Image extraction requires a vision LLM provider")
            if detected.startswith("audio/"):
                raise UnsupportedContentError(
                    "Audio requires a separate ASR provider; the vision model is not an audio transcriber"
                )
            raise UnsupportedContentError("Unsupported attachment type; convert to PDF, DOCX, or UTF-8 text")
        except asyncio.CancelledError:
            event.status, event.error_type = "cancelled", "CancelledError"
            raise
        except Exception as exc:
            event.status, event.error_type = "error", type(exc).__name__
            raise
        finally:
            event.latency_ms = (time.perf_counter() - started) * 1000
            await safe_log(self.tracker, event)
