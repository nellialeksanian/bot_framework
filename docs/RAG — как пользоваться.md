# RAG-пайплайн (A3): как пользоваться

Практическое руководство по `botkit.rag` — коду в `src/botkit/rag/`. Про архитектурные решения и инварианты см. раздел A3 в [«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md); здесь — как это реально вызывать.

---

## Общая схема

```
Файл (CSV/PDF/DOCX)
   │
   ▼
ingest_csv/ingest_pdf/ingest_docx   (botkit.rag.ingest)
   │  — извлекает текст, чанкует по предложениям, проверяет качество
   ▼
list[Chunk]  +  IngestReport
   │
   ▼
ChromaVectorStore.ingest()   (botkit.rag.chroma_store)
   │  — вызывает нужный ingest_* по расширению файла
   │  — считает эмбеддинги через self.embedder
   │  — пишет в ChromaDB на диске
   ▼
ChromaDB (persist_path/collection)
   │
   ├── ChromaVectorStore.search()         — поиск в ОДНОЙ коллекции
   │
   └── search_across()                     — поиск в НЕСКОЛЬКИХ коллекциях
        (botkit.rag.multi_search)             параллельно, свой k на каждую
```

Живой рабочий пример всего этого — папка `rag_demo/` рядом с `bot_framework/`: `1_build_knowledge_base.py` (ingest, несколько коллекций), `2_skill_search.py` (поиск в одну), `3_multi_search.py` (поиск в несколько), `4_progress_demo.py` (прогресс на большом PDF).

---

## Шаг 1 — создание БД

```python
from botkit.rag.chroma_store import ChromaVectorStore
from botkit.rag.embeddings import EmbedderConfig

store = ChromaVectorStore(
    embedder_config=EmbedderConfig(
        name="bge-m3-local",      # произвольное имя — сохраняется в metadata коллекции
        kind="huggingface",        # или "infinity"
        model="BAAI/bge-m3",
        device="cpu",
    ),
    persist_path="db",             # папка на диске — переживает рестарт
    collection="philosophy_texts", # имя коллекции внутри этой папки
)

report = await store.ingest("documents/beauvoir.pdf")
print(report.chunks_added, report.warnings, report.errors)
```

Что происходит внутри `ingest()`:
1. Маршрутизация по расширению файла на `ingest_csv`/`ingest_pdf`/`ingest_docx` — парсинг, sentence-aware чанкинг, проверка сканов/кракозябр, ещё без эмбеддингов.
2. `_add_chunks()` — вот здесь считаются эмбеддинги и пишутся вектора в Chroma на диске.

Конструктор `ChromaVectorStore(...)` уже проверяет: если коллекция существует и построена **другим** эмбеддером — `EmbedderMismatchError`, а не тихая деградация релевантности поиска.

### Прогресс на больших файлах

```python
def on_progress(done, total):
    print(f"{done}/{total}", end="\r", flush=True)

await store.ingest("big_book.pdf", on_progress=on_progress)
```

Вызывается после каждой страницы (PDF) / главы (DOCX) / строки (CSV). Библиотека сама ничего не печатает — вы решаете, как показать (print, tqdm, логгер, ничего).

Готовая обёртка на `tqdm` (нужен `pip install tqdm` или `pip install botkit[progress]`):

```python
from botkit.rag.progress import tqdm_progress

report = await store.ingest("big_book.pdf", on_progress=tqdm_progress("big_book.pdf"))
```

Прогресс-бар тогда считается по числу страниц/глав/строк (единица `on_progress`), не по числу итоговых чанков — итоговых чанков может получиться больше или меньше, это отдельная цифра в `report.chunks_added`.

### Несколько коллекций в одной базе

Один `ChromaVectorStore` = одна коллекция (жёстко, из-за защиты от embedder mismatch). Для нескольких коллекций — несколько store, но с одним и тем же `persist_path`:

```python
COLLECTIONS = {
    "philosophy_texts": ["documents/philosophy.csv"],
    "political_theory_texts": ["documents/political_theory.csv"],
}

for name, paths in COLLECTIONS.items():
    store = ChromaVectorStore(embedder_config=EMBEDDER, persist_path="db", collection=name)
    for path in paths:
        await store.ingest(path)
```

Физически: одна папка `db/`, один `chroma.sqlite3`, но по одной UUID-подпапке (векторный индекс) на каждую коллекцию.

---

## Шаг 2 — поиск в одну коллекцию

```python
chunks = await store.search("denying your own freedom", k=3)
```

Один вызов `similarity_search()` внутри, результат — `list[Chunk]`.

---

## Шаг 3 — поиск в несколько коллекций одновременно

```python
from botkit.rag.multi_search import search_across

stores = {
    "philosophy_texts": philosophy_store,
    "political_theory_texts": political_store,
}

results = await search_across(
    stores,
    query="how is legitimate power related to freedom",
    collection_k={"philosophy_texts": 2, "political_theory_texts": 1},
)
# -> {"philosophy_texts": [Chunk, Chunk], "political_theory_texts": [Chunk]}
```

`collection_k` задаёт одновременно И набор коллекций для поиска (её ключи), И число чанков на каждую (её значения). Коллекция без соответствующего store в `stores` — `KeyError`, не молчаливый пропуск. Поиск идёт параллельно (`asyncio.gather`), не по очереди.

---

## Финальный `Chunk` — что реально внутри

**Из CSV** (`ingest_csv`):
```python
Chunk(
    text="Bad faith means denying one's own freedom and transcendence.",
    source="beauvoir.pdf",
    page=None,                    # у CSV нет страниц
    chunk_index=0,
    metadata={
        "collection": "philosophy_texts",
        "source": "beauvoir.pdf",
        "ingested_at": "2026-09-16T11:43:16.509583+00:00",
        "category": "philosophy", # любая доп. колонка CSV попадает сюда как есть
    },
)
```

**Из PDF** (`ingest_pdf`):
```python
Chunk(
    text="...",
    source="Max Weber - Politics as a Vocation.pdf",
    page=1,
    chunk_index=0,
    metadata={
        "ingested_at": "2026-09-16T11:43:33.883121+00:00",
        "source": "Max Weber - Politics as a Vocation.pdf",
        "collection": "weber",
        "page": 1,
    },
)
```

**Из DOCX** (`ingest_docx`): то же, но вместо `page` — `metadata["chapter"]`, если распознан заголовок Heading-стиля.

---

## Что реально идёт в промпт модели

```python
from botkit.rag.chroma_store import format_citation

prompt = f"{format_citation(chunk)}\n{chunk.text}"
# "Source: beauvoir.pdf\nBad faith means denying one's own freedom and transcendence."
```

`format_citation()` собирает `Source: ..., page: ..., chapter: ...` (то, что есть) — в модель обычно идёт не весь `Chunk`, а эта строка + `chunk.text`. Остальные поля (`chunk_index`, `collection`, `ingested_at`) — служебные, для отладки/фильтрации, не для прямого показа модели (хотя навык вправе решить иначе, например добавить `category` в промпт, если это полезно).

---

## Проверки качества при ingest (PDF)

- **Скан-детект** (`quality.looks_like_scan`) — страница с < 30 символами текста считается сканом без текстового слоя.
- **Кракозябры** (`quality.looks_garbled`) — эвристика по доле control/private-use символов.

Проблемная страница помечается в `IngestReport.warnings` (например `likely_scanned_page_25`) и **не индексируется**, но остальные страницы того же документа индексируются нормально — fail-closed по странице, не по всему файлу.

## Инварианты

- Каждый `Chunk` обязан иметь `source` — CSV-строка без него не индексируется (только warning), а не попадает в БД с пустым полем.
- Коллекция помнит, каким эмбеддером построена — попытка открыть другим эмбеддером даёт `EmbedderMismatchError`, а не тихую деградацию поиска.
