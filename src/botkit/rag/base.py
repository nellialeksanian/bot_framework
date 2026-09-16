# A3. RAG Ingestion & Retrieval
# Источник: "Модули фреймворка — приоритет.md", раздел A3.
# Индексация PDF/DOCX в векторную БД, поиск релевантных фрагментов с метаданными источника.

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

OnProgress = Callable[[int, int], None]
"""on_progress(done, total) — вызывается после каждой обработанной единицы
(строка CSV / страница PDF / глава DOCX). total может быть 0, если общее
число заранее неизвестно — вызывающий код сам решает, как это показать
(print, tqdm, логгер, прогресс-бар в UI бота); реализация ingest() не
форматирует вывод сама."""


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
    async def ingest(
        self,
        documents_path: str,
        collection: str,
        *,
        on_progress: OnProgress | None = None,
    ) -> IngestReport:
        ...

    async def search(self, query: str, k: int) -> list[Chunk]:
        ...


# Поиск по нескольким коллекциям одновременно — не метод VectorStore
# (каждый VectorStore отвечает за одну коллекцию), а отдельная функция
# над несколькими уже открытыми store: см. botkit.rag.multi_search.search_across().
#
#   async def search_across(
#       stores: dict[str, VectorStore],
#       query: str,
#       collection_k: dict[str, int],
#   ) -> dict[str, list[Chunk]]: ...
#
# collection_k задаёт одновременно набор коллекций для поиска (её ключи)
# и число чанков на каждую (её значения) — не общий k на все сразу.


def format_citation(chunk: Chunk) -> str:
    """Единый формат 'Source: ... page: ...'."""
    ...
