# B5. Evidence & Citation Layer — SC04 + SC05
# Источник: "Модули фреймворка — приоритет.md", раздел B5.
# Проверка, что цитируемый фрагмент действительно поддерживает утверждение.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class EvidenceLink:
    claim: str
    chunk_ref: str
    entailment: Literal["SUPPORTED", "PARTIAL", "CONFLICTING", "UNKNOWN"]


class EvidenceChecker(Protocol):
    async def verify(self, claim: str, chunk) -> EvidenceLink:
        """chunk: botkit.rag.base.Chunk
        Отдельный (более дешёвый/быстрый) LLM-вызов или эвристика,
        ДО того как claim попадёт в финальный ответ."""
        ...
