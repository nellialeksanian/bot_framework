# A3. RAG Ingestion & Retrieval — поиск по нескольким коллекциям одновременно.
#
# Архитектурное решение: НЕ отдельный класс (MultiVectorStore), а функция
# над уже созданными ChromaVectorStore. Один ChromaVectorStore всегда
# отвечает ровно за одну коллекцию (см. EmbedderMismatchError в
# chroma_store.py) — это намеренное ограничение, не случайность. Поэтому
# "поиск по нескольким коллекциям" — это не новая обязанность store,
# а оркестрация уже существующих store снаружи: параллельные вызовы
# их собственного search(), просто выполненные вместе через asyncio.gather.

from __future__ import annotations

import asyncio

from botkit.rag.base import Chunk
from botkit.rag.chroma_store import ChromaVectorStore


async def search_across(
    stores: dict[str, ChromaVectorStore],
    query: str,
    collection_k: dict[str, int],
) -> dict[str, list[Chunk]]:
    """Ищет query параллельно в нескольких коллекциях.

    stores — {имя_коллекции: уже созданный ChromaVectorStore для неё}.
    collection_k — {имя_коллекции: сколько чанков достать именно из неё} —
    выбор И набора коллекций (ключи словаря), И числа чанков на каждую
    (значения) в одном месте: коллекция, не упомянутая в collection_k,
    не участвует в поиске вообще, даже если store для неё есть в stores.

    Каждый Chunk уже несёт "collection" в metadata (проставляется при
    ingest — см. ChromaVectorStore._add_chunks) — это позволяет опознать
    происхождение чанка, даже если результаты из разных коллекций потом
    объединяются в один список вызывающим кодом.

    Отсутствие имени в stores для запрошенной в collection_k коллекции —
    ошибка конфигурации вызывающего кода, а не тихо пропущенная коллекция.
    """
    missing = set(collection_k) - set(stores)
    if missing:
        raise KeyError(
            f"collection_k references collections with no store: {sorted(missing)}. "
            f"Available stores: {sorted(stores)}."
        )

    async def _search_one(name: str, k: int) -> tuple[str, list[Chunk]]:
        chunks = await stores[name].search(query, k=k)
        return name, chunks

    tasks = [_search_one(name, k) for name, k in collection_k.items()]
    results = await asyncio.gather(*tasks)

    return dict(results)
