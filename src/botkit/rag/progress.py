# A3. RAG Ingestion & Retrieval — готовые on_progress-обёртки.
#
# base.py сознательно не форматирует прогресс сам (OnProgress = голый
# (done, total) callback) — какой библиотекой рисовать прогресс-бар,
# решает вызывающий код. tqdm — самый частый выбор для этого, поэтому
# здесь есть готовая обёртка, но она НЕ обязательная зависимость пакета:
# tqdm импортируется лениво, внутри функции, а не на уровне модуля —
# ingest()/ChromaVectorStore по-прежнему работают без tqdm, если он не
# установлен и не используется.

from __future__ import annotations

from botkit.rag.base import OnProgress


def tqdm_progress(label: str | None = None) -> OnProgress:
    """Возвращает on_progress-callback, рисующий прогресс через tqdm.

    Пример:
        report = await store.ingest("book.pdf", on_progress=tqdm_progress("book.pdf"))

    Создаёт один tqdm-бар на файл — обновляется по мере вызовов
    on_progress(done, total). Требует установленный tqdm:
        pip install "botkit[progress]"   (или просто: pip install tqdm)
    """
    try:
        from tqdm import tqdm
    except ImportError as e:
        raise ImportError(
            "tqdm_progress() requires tqdm — install it with `pip install tqdm` "
            "or `pip install botkit[progress]`."
        ) from e

    bar: "tqdm | None" = None

    def _on_progress(done: int, total: int) -> None:
        nonlocal bar
        if bar is None:
            bar = tqdm(total=total if total > 0 else None, desc=label, unit="item")
        bar.n = done
        if total > 0 and done >= total:
            bar.close()  # close() делает свой финальный refresh — отдельный не нужен
        else:
            bar.refresh()

    return _on_progress
