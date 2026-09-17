# A1. LLM Provider Gateway
# Источник: "Модули фреймворка — приоритет.md", раздел A1.
# Единая точка вызова LLM независимо от бэкенда, с устойчивым к "грязному" выводу парсингом.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class LLMResponse:
    response: str
    raw: str
    parse_status: Literal["clean_json", "regex_fallback", "raw_text_fallback"]
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    model_name: str

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> LLMResponse:
        ...

    async def aclose(self) -> None:
        ...


def load_llm(provider: str) -> LLMProvider:
    """Фабрика, скрывающая выбор провайдера от навыков."""
    ...
