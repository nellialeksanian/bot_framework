# A3. RAG Ingestion & Retrieval — фабрика эмбеддеров.
# Источник наработки: /Users/nellyaleksanyan/Desktop/organizer_bot/app/vk_bot.py:1236-1270
# — тот же выбор (локальная модель через HuggingFace Hub, либо HTTP-сервис
# Infinity по URL), перенесённый из инлайн if/elif в фабрику с явным
# протоколом выбора, вместо переключателя строкой из os.getenv.
#
# Важный инвариант, задокументированный в первоисточнике: эмбеддер поиска
# ДОЛЖЕН совпадать с эмбеддером, которым построена коллекция — иначе
# релевантность тихо ломается (комментарий organizer_bot: "~0.03-0.05
# вместо ожидаемых высоких значений"). Здесь эта проверка вынесена в
# ChromaVectorStore (chroma_store.py), а не в саму фабрику — фабрика
# только создаёт эмбеддер, store.py решает, разрешено ли его использовать
# с конкретной уже существующей коллекцией.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


class Embedder(Protocol):
    """Совместим с langchain Embeddings (embed_documents/embed_query) —
    и HuggingFaceEmbeddings, и InfinityEmbeddings уже реализуют этот протокол,
    отдельная обёртка не нужна."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    def embed_query(self, text: str) -> list[float]:
        ...


@dataclass
class EmbedderConfig:
    name: str
    """Идентификатор модели/сервиса — сохраняется в metadata коллекции
    для проверки согласованности между ingest и search (см. chroma_store.py)."""

    kind: Literal["huggingface", "infinity"]
    model: str = "BAAI/bge-m3"
    device: str = "cpu"
    infinity_api_url: str | None = None
    """Обязателен при kind == "infinity"."""


def load_embedder(config: EmbedderConfig) -> Embedder:
    if config.kind == "huggingface":
        # Ленивый импорт — HuggingFaceEmbeddings тянет torch/transformers,
        # которые не нужны вообще при kind == "infinity" (там просто HTTP-клиент).
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(
            model_name=config.model,
            model_kwargs={"device": config.device},
        )

    if config.kind == "infinity":
        if not config.infinity_api_url:
            raise ValueError("infinity_api_url is required when kind='infinity'")

        from langchain_community.embeddings import InfinityEmbeddings

        return InfinityEmbeddings(model=config.model, infinity_api_url=config.infinity_api_url)

    raise ValueError(f"Unknown embedder kind: {config.kind!r}, expected 'huggingface' or 'infinity'")
