# A3. RAG Ingestion & Retrieval — конкретная реализация VectorStore поверх
# ChromaDB. Источник наработок: build_raci_rag.py (PersistentClient, cosine
# distance для unit-normed bge-m3 векторов, идемпотентный ingest по id) +
# organizer_bot/app/vk_bot.py (выбор эмбеддера, embedder/collection
# согласованность). ingest() маршрутизирует по расширению файла на функции
# из ingest.py (CSV/PDF/DOCX) — сама эта реализация отвечает только за
# запись в Chroma и защиту от embedder mismatch, не за извлечение текста.

from __future__ import annotations

import hashlib
import os

import chromadb

from botkit.rag.base import Chunk, IngestReport
from botkit.rag.embeddings import Embedder, EmbedderConfig, load_embedder
from botkit.rag.ingest import OnProgress, ingest_csv, ingest_docx, ingest_pdf

_EMBEDDER_NAME_KEY = "botkit_embedder_name"
"""Ключ в collection_metadata Chroma, под которым хранится имя эмбеддера,
которым коллекция была построена — источник инварианта: комментарий в
organizer_bot/app/vk_bot.py:1238-1246 про тихую поломку релевантности при
рассинхроне эмбеддера индексации и эмбеддера поиска."""


class EmbedderMismatchError(Exception):
    """Коллекция была построена другим эмбеддером — использовать текущий
    означало бы воспроизвести проблему из organizer_bot: поиск с score
    ~0.03-0.05 вместо ожидаемых значений, без явной ошибки где-либо."""


def _chunk_id(chunk: Chunk) -> str:
    # Стабильный id из source+chunk_index — повторный ingest того же файла
    # обновляет существующие записи вместо дублирования (идемпотентность,
    # как cunk_id в build_raci_rag.py, но не требует отдельной колонки).
    raw = f"{chunk.source}::{chunk.chunk_index}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ChromaVectorStore:
    """Реализация botkit.rag.base.VectorStore поверх персистентной ChromaDB."""

    def __init__(
        self,
        *,
        embedder_config: EmbedderConfig,
        persist_path: str,
        collection: str,
    ) -> None:
        self.embedder_config = embedder_config
        self.embedder: Embedder = load_embedder(embedder_config)
        self.persist_path = persist_path
        self.collection_name = collection

        self._client = chromadb.PersistentClient(path=persist_path)
        self._store = self._open_collection()

    def _open_collection(self):
        from langchain_chroma import Chroma

        existing_names = {c.name for c in self._client.list_collections()}
        is_new = self.collection_name not in existing_names

        store = Chroma(
            client=self._client,
            collection_name=self.collection_name,
            embedding_function=self.embedder,
            # bge-m3 и большинство современных sentence-эмбеддеров дают
            # unit-normed векторы — cosine distance держит relevance-score
            # в осмысленном диапазоне [0, 1] (см. build_raci_rag.py).
            collection_metadata={"hnsw:space": "cosine"},
        )

        raw_collection = self._client.get_collection(self.collection_name)
        stored_name = (raw_collection.metadata or {}).get(_EMBEDDER_NAME_KEY)

        if is_new or stored_name is None:
            # modify() требует полный metadata-объект и запрещает менять
            # "hnsw:space" — исключаем системные hnsw:* ключи из merge,
            # передаём только наши собственные плюс новый embedder-маркер.
            user_metadata = {
                k: v for k, v in (raw_collection.metadata or {}).items() if not k.startswith("hnsw:")
            }
            raw_collection.modify(metadata={**user_metadata, _EMBEDDER_NAME_KEY: self.embedder_config.name})
        elif stored_name != self.embedder_config.name:
            raise EmbedderMismatchError(
                f"Collection '{self.collection_name}' was built with embedder "
                f"'{stored_name}', but current embedder is '{self.embedder_config.name}'. "
                f"Search results would be silently unreliable — use the original "
                f"embedder, or ingest into a new collection."
            )

        return store

    async def ingest(
        self,
        documents_path: str,
        collection: str | None = None,
        *,
        on_progress: OnProgress | None = None,
    ) -> IngestReport:
        """collection — принято для совместимости с VectorStore Protocol;
        эта реализация всегда пишет в self.collection_name, заданную в
        конструкторе (одна коллекция = один store = один проверенный
        эмбеддер, см. EmbedderMismatchError).

        on_progress(done, total) — прогресс парсинга/чанкинга файла
        (страница PDF / глава DOCX / строка CSV), вызывается синхронно
        внутри ingest_csv/ingest_pdf/ingest_docx. Запись самих чанков в
        Chroma (self._add_chunks) идёт одним батчем после парсинга и
        прогресс не эмитит — на корпусах, использованных при разработке
        (тысячи чанков), эта часть быстрее самого парсинга на порядок
        и не была узким местом при замере (см. историю разработки rag/)."""
        if collection is not None and collection != self.collection_name:
            raise ValueError(
                f"This ChromaVectorStore instance is bound to collection "
                f"'{self.collection_name}', not '{collection}'. "
                f"Create a separate ChromaVectorStore for a different collection."
            )

        ext = os.path.splitext(documents_path)[1].lower()
        if ext == ".csv":
            chunks, report = ingest_csv(documents_path, on_progress=on_progress)
        elif ext == ".pdf":
            chunks, report = ingest_pdf(documents_path, on_progress=on_progress)
        elif ext == ".docx":
            chunks, report = ingest_docx(documents_path, on_progress=on_progress)
        else:
            return IngestReport(
                chunks_added=0, warnings=[], errors=[f"unsupported_extension: {ext}"]
            )

        if chunks:
            self._add_chunks(chunks)

        return report

    def _add_chunks(self, chunks: list[Chunk]) -> None:
        from langchain_core.documents import Document

        ids = [_chunk_id(c) for c in chunks]
        docs = [
            Document(page_content=c.text, metadata={**c.metadata, "collection": self.collection_name})
            for c in chunks
        ]
        self._store.add_documents(docs, ids=ids)

    async def search(self, query: str, k: int) -> list[Chunk]:
        results = self._store.similarity_search(query, k=k)
        return [
            Chunk(
                text=doc.page_content,
                source=doc.metadata.get("source", ""),
                page=doc.metadata.get("page"),
                chunk_index=doc.metadata.get("chunk_index", 0),
                metadata=doc.metadata,
            )
            for doc in results
        ]

    # Поиск сразу по нескольким коллекциям — это не метод одного store
    # (у одного ChromaVectorStore всегда ровно одна коллекция, см. ingest()
    # выше), а отдельная функция над несколькими уже созданными store:
    # см. botkit.rag.multi_search.search_across().


def format_citation(chunk: Chunk) -> str:
    parts = [f"Source: {chunk.source}"]
    if chunk.page is not None:
        parts.append(f"page: {chunk.page}")
    if "chapter" in chunk.metadata:
        parts.append(f"chapter: {chunk.metadata['chapter']}")
    return ", ".join(parts)
