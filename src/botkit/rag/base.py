# A3. RAG Ingestion & Retrieval
# Источник: "Модули фреймворка — приоритет.md", раздел A3.
# Индексация PDF/DOCX в векторную БД, поиск релевантных фрагментов с метаданными источника.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class Chunk:
    text: str
    source: str
    page: int | None
    chunk_index: int
    metadata: dict


@dataclass
class IngestReport:
    chunks_added: int
    warnings: list[str]
    errors: list[str]


class VectorStore(Protocol):
    async def ingest(self, documents_path: str, collection: str) -> IngestReport:
        ...

    async def search(
        self, query: str, k: int, strategy: Literal["chunks", "full_case"] = "chunks"
    ) -> list[Chunk]:
        ...

    async def search_multi(self, query: str, collections: list[str]) -> dict[str, list[Chunk]]:
        ...


def format_citation(chunk: Chunk) -> str:
    """Единый формат 'Source: ... page: ...'."""
    ...
