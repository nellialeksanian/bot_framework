from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

PDFMode = Literal["auto", "text", "vision"]


class ExtractionError(ValueError):
    """Safe, user-facing error; never contains document contents or credentials."""


class UnsupportedContentError(ExtractionError):
    pass


class ExtractionLimitError(ExtractionError):
    pass


@dataclass(frozen=True)
class ExtractionConfig:
    pdf_mode: PDFMode = "auto"
    max_bytes: int = 20 * 1024 * 1024
    max_pages: int = 30
    max_vision_pages: int = 10
    max_text_chars: int = 200_000
    max_image_pixels: int = 25_000_000
    render_max_pixels: int = 4_000_000
    render_dpi: int = 144
    max_rendered_bytes: int = 50 * 1024 * 1024
    max_archive_bytes: int = 100 * 1024 * 1024
    max_archive_entries: int = 2000
    max_cells: int = 50_000
    pdf_min_text_chars: int = 30
    vision_timeout: float = 180
    vision_max_tokens: int = 8192

    def __post_init__(self):
        if self.pdf_mode not in {"auto", "text", "vision"}:
            raise ValueError("pdf_mode must be auto, text or vision")
        for name, value in vars(self).items():
            if name != "pdf_mode" and (not isinstance(value, (int, float)) or value <= 0):
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls) -> ExtractionConfig:
        return cls(
            pdf_mode=os.getenv("EXTRACTION_PDF_MODE", "auto"),
            max_bytes=int(os.getenv("MAX_ATTACHMENT_BYTES", str(20 * 1024 * 1024))),
            max_pages=int(os.getenv("EXTRACTION_MAX_PAGES", "30")),
            max_vision_pages=int(os.getenv("EXTRACTION_MAX_VISION_PAGES", "10")),
            max_text_chars=int(os.getenv("EXTRACTION_MAX_TEXT_CHARS", "200000")),
            render_dpi=int(os.getenv("EXTRACTION_RENDER_DPI", "144")),
            vision_timeout=float(os.getenv("EXTRACTION_VISION_TIMEOUT", "180")),
        )

    def check_bytes(self, data: bytes) -> None:
        if not data:
            raise ExtractionError("Empty attachment")
        if len(data) > self.max_bytes:
            raise ExtractionLimitError("Attachment exceeds extraction size limit")

    def check_text(self, text: str) -> None:
        if len(text) > self.max_text_chars:
            raise ExtractionLimitError("Extracted text exceeds character limit")
