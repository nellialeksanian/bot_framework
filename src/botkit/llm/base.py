"""A1 public contracts. Existing response fields and imports remain compatible."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .parsing import ParseStatus


@dataclass
class LLMResponse:
    response: str
    raw: str
    parse_status: ParseStatus
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    usage_known: bool = False
    model: str = ""
    provider: str = ""
    request_id: str = ""
    finish_reason: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None


class LLMProvider(Protocol):
    model_name: str

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0, **options: Any) -> LLMResponse: ...
    async def aclose(self) -> None: ...

    async def ainvoke_with_image_b64(
        self, prompt: str, image_b64: str, *, mime_type: str = "image/png", **options: Any
    ) -> LLMResponse: ...


class AudioTranscriptionProvider(Protocol):
    """Optional A1 capability: use a separately configured ASR model, not a vision model."""

    async def atranscribe(
        self,
        data: bytes,
        *,
        mime_type: str,
        filename: str,
        timeout: float | None = None,
        language: str | None = None,
    ) -> LLMResponse: ...


def load_llm(provider: str | None = None, **options: Any) -> LLMProvider:
    """Lazy import keeps contracts independent of concrete clients."""
    from .factory import load_llm as factory

    return factory(provider, **options)
