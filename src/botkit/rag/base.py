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


# Поиск по нескольким коллекциям одновременно — не метод VectorStore
# (каждый VectorStore отвечает за одну коллекцию), а отдельная функция
# над несколькими уже открытыми store: см. botkit.rag.multi_search.search_across().


def format_citation(chunk: Chunk) -> str:
    """Единый формат 'Source: ... page: ...'."""
    ...
