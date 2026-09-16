# A3. RAG Ingestion & Retrieval — чанкинг с границей по предложению.
# Заменяет RecursiveCharacterTextSplitter (режет по числу символов, может
# оборвать предложение/слово на середине) на упаковку целых предложений —
# nltk.sent_tokenize выбран по замеру: ~11x медленнее посимвольного regex-
# поиска границы (обзор utils/ciceron/.../obzor_chunker.py:find_sentence_boundary),
# но на 3 порядка быстрее pysbd, и надёжнее простого regex на сокращениях/
# инициалах ("т.е.", "И. И. Иванов").

from __future__ import annotations

import nltk


def _ensure_punkt() -> None:
    for resource in ("tokenizers/punkt", "tokenizers/punkt_tab"):
        try:
            nltk.data.find(resource)
        except LookupError:
            nltk.download(resource.split("/")[-1], quiet=True)


def split_into_sentences(text: str, *, language: str = "russian") -> list[str]:
    """Разбивает текст на предложения. language — имя модели nltk punkt
    ("russian", "english", ...), не код ISO."""
    _ensure_punkt()
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return []
    return nltk.tokenize.sent_tokenize(cleaned, language=language)


def pack_sentences_into_chunks(
    sentences: list[str],
    *,
    chunk_size: int,
    chunk_overlap: int = 0,
) -> list[str]:
    """Упаковывает предложения в чанки до chunk_size символов, никогда не
    разрывая предложение пополам. Чанк может быть немного длиннее chunk_size,
    если одно предложение само по себе длиннее лимита — в этом случае оно
    остаётся целым чанком (лучше один длинный чанк, чем обрубленное предложение).

    chunk_overlap — сколько последних предложений предыдущего чанка
    повторяются в начале следующего, для сохранения контекста на границе.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence_len = len(sentence)
        if current and current_len + 1 + sentence_len > chunk_size:
            chunks.append(" ".join(current))
            if chunk_overlap > 0:
                overlap_sentences: list[str] = []
                overlap_len = 0
                for s in reversed(current):
                    if overlap_len + len(s) > chunk_overlap:
                        break
                    overlap_sentences.insert(0, s)
                    overlap_len += len(s) + 1
                current = overlap_sentences
                current_len = overlap_len
            else:
                current = []
                current_len = 0

        current.append(sentence)
        current_len += sentence_len + 1

    if current:
        chunks.append(" ".join(current))

    return chunks


def sentence_aware_chunks(
    text: str,
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
    language: str = "russian",
) -> list[str]:
    """Разбивает текст на чанки заданного размера, каждый чанк — целое
    число предложений, никогда не обрывается на середине слова/предложения."""
    sentences = split_into_sentences(text, language=language)
    return pack_sentences_into_chunks(sentences, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
