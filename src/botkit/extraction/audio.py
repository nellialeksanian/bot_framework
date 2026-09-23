from __future__ import annotations

from botkit.llm.base import AudioTranscriptionProvider

from .base import ExtractedContent
from .config import ExtractionConfig


class AudioExtractor:
    kind = "audio"
    extensions = {
        "audio/wav": "wav",
        "audio/x-wav": "wav",
        "audio/mpeg": "mp3",
        "audio/ogg": "ogg",
        "audio/mp4": "m4a",
        "audio/flac": "flac",
        "audio/webm": "webm",
    }

    def __init__(self, transcriber: AudioTranscriptionProvider, config: ExtractionConfig | None = None):
        self.transcriber, self.config = transcriber, config or ExtractionConfig()

    async def supports(self, mime_type: str) -> bool:
        return mime_type in self.extensions

    async def extract(self, data: bytes, mime_type: str) -> ExtractedContent:
        self.config.check_bytes(data)
        response = await self.transcriber.atranscribe(
            data,
            mime_type=mime_type,
            filename="audio." + self.extensions[mime_type],
            timeout=self.config.vision_timeout,
        )
        self.config.check_text(response.raw)
        warnings = ["audio_transcript_unverified"]
        if not response.raw.strip():
            warnings.append("empty_extraction")
        return ExtractedContent(
            "transcript",
            response.raw,
            None,
            warnings,
            "low",
            {"method": "asr", "model": response.model, "provider": response.provider},
        )
