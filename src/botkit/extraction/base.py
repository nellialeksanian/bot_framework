# A5. Content Extraction Layer
# Источник: "Модули фреймворка — приоритет.md", раздел A5.
# Превращение сырого вложения (байты + MIME-тип) в текст/структуру/транскрипт.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class ExtractedContent:
    kind: Literal["text", "structured", "transcript"]
    text: str | None
    structured_data: dict | None
    warnings: list[str]
    source_confidence: Literal["high", "low", "unknown"]


class Extractor(Protocol):
    kind: Literal["document", "image", "audio"]

    async def supports(self, mime_type: str) -> bool:
        ...

    async def extract(self, data: bytes, mime_type: str) -> ExtractedContent:
        ...


class ContentExtractionService(Protocol):
    def register(self, extractor: Extractor) -> None:
        ...

    async def extract(self, attachment) -> ExtractedContent:
        """attachment: botkit.transport.base.Attachment"""
        ...
