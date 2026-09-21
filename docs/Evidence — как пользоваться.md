# Evidence & Citation Layer (B5): как пользоваться

Практическое руководство по `botkit.evidence` — коду в `src/botkit/evidence/`.
Про архитектурные решения, происхождение из SC04+SC05 и разбор портфеля см.
раздел B5 в [«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md);
здесь — как это реально вызывать.

---

## Зачем это отдельный модуль, а не часть RAG (A3)

`botkit.rag` (A3) отвечает за retrieval: находит чанки, семантически похожие
на запрос. Он не проверяет, что найденный чанк *поддерживает* конкретное
утверждение — просто отдаёт то, что оказалось рядом по близости эмбеддингов.

Инвариант 5.7 карты портфеля («Grounding: citation не равна evidence»):
`retrieved=true` недостаточно. Система может найти тематически близкий
фрагмент и сделать вывод, которого источник не поддерживает. Retrieval
score и entailment state обязаны быть разными объектами. B5 — это именно
entailment state: отдельный шаг ПОСЛЕ retrieval, ДО того как утверждение
попадёт в финальный ответ пользователю.

```
botkit.rag.VectorStore.search(query, k)     (A3 — retrieval)
   │
   ▼
list[Chunk]                                  — семантически похожие фрагменты
   │
   ▼
EvidenceChecker.verify(claim, chunk)         (B5 — entailment, ПОСЛЕ retrieval)
   │
   ▼
EvidenceLink(claim, chunk_ref, entailment)   — SUPPORTED | PARTIAL | CONFLICTING | UNKNOWN
   │
   ▼
EvidenceStore.record(link)                   — append-only лог, переживает рестарт
```

---

## Общая схема

```
LLMEvidenceChecker(llm)              (botkit.evidence.llm_checker)
   │  — второй, отдельный LLM-вызов ПОСЛЕ основного ответа,
   │    по тому же паттерну, что LLMRevisionClassifier (B2)
   │    и LLMRoleBoundaryClassifier (C1)
   ▼
EvidenceLink(claim, chunk_ref, source, entailment, created_at)
   │
   ├── verify_and_record(checker, store, claim, chunk)   (botkit.evidence.base)
   │      — связывает проверку и запись в один вызов
   ▼
SQLiteEvidenceStore(db_path)         (botkit.evidence.sqlite_store)
   │  — один файл на диске, append-only, переживает рестарт процесса
   │
   ├── get_for_claim(claim)       → [EvidenceLink, ...]  все проверки claim
   └── get_conflicting(claim)     → [EvidenceLink, ...]  только CONFLICTING
```

`chunk_ref` в `EvidenceLink` собирается из `Chunk.source` и
`Chunk.chunk_index` (A3) — `EvidenceLink` не хранит сам текст чанка, только
факт проверки; содержимое чанка остаётся в `VectorStore`.

---

## Шаг 1 — создать хранилище

```python
from botkit.evidence.sqlite_store import SQLiteEvidenceStore

evidence_store = SQLiteEvidenceStore("logs/evidence.sqlite3")
```

Один раз при старте бота, как `SQLiteRubricStore` в B3. Конструктор сам
создаёт файл и таблицу (`evidence_links`), если её ещё нет.

---

## Шаг 2 — создать checker поверх LLMProvider

```python
from botkit.evidence.llm_checker import LLMEvidenceChecker

evidence_checker = LLMEvidenceChecker(llm)  # llm: LLMProvider из A1, load_llm()
```

Один экземпляр на бот-процесс, переиспользует тот же `LLMProvider`, что и
остальные навыки — не заводит отдельного клиента.

---

## Шаг 3 — проверить claim против найденного chunk

```python
from botkit.evidence.base import verify_and_record

chunks = await vector_store.search(query, k=3)  # A3

for chunk in chunks:
    link = await verify_and_record(evidence_checker, evidence_store, claim, chunk)

    if link.entailment == "SUPPORTED":
        # можно процитировать chunk в ответе
        ...
    elif link.entailment == "CONFLICTING":
        # источники расходятся — сказать об этом явно, не выбирать один молча
        ...
    else:  # PARTIAL или UNKNOWN
        # не показывать как готовое основание для claim
        ...
```

`verify_and_record()` — тонкая функция, связывающая `EvidenceChecker.verify()`
(классификация, без побочных эффектов) и `EvidenceStore.record()` (запись,
append-only) в один вызов. Можно использовать их и по отдельности — оба
остаются независимыми `Protocol` (тот же приём, что `sync_package()` в B3).

---

## Шаг 4 — прочитать историю проверок

```python
all_checks = await evidence_store.get_for_claim(claim)
# [EvidenceLink, ...] — все проверки этого claim против разных чанков,
# в том числе из прошлых сообщений/рестартов процесса

conflicts = await evidence_store.get_conflicting(claim)
# только entailment == "CONFLICTING" — прямой ответ на вопрос
# "источники расходятся?", не нужно фильтровать весь список вручную
```

---

## `UNKNOWN` — легитимный результат, не ошибка

Инвариант B5: `entailment == "UNKNOWN"` — допустимый исход (fail-closed),
не сбой, который нужно скрыть или додумать за модель. `LLMEvidenceChecker`
возвращает `UNKNOWN` в двух случаях:

- модель явно вернула `{"entailment": "UNKNOWN"}` — по фрагменту
  невозможно уверенно определить отношение к claim;
- ответ модели не удалось распарсить вовсе (нет валидного JSON с полем
  `entailment`) — тот же fail-closed приём, что `is_revision()` в B2
  возвращает `None`, а не угадывает `True`/`False`.

Навык обязан различать «источник поддерживает claim» и «источник рядом по
теме, но неясно, поддерживает ли» — компенсировать нехватку evidence
красивым синтаксисом запрещено (см. раздел 5.7 карты v2.2).

---

## Финальный `EvidenceLink` — что реально внутри

```python
EvidenceLink(
    evidence_link_id="346a98dd-...",
    claim="вода кипит при 100°C на уровне моря",
    chunk_ref="lecture_04.pdf::7",
    source="lecture_04.pdf",
    entailment="SUPPORTED",           # SUPPORTED | PARTIAL | CONFLICTING | UNKNOWN
    created_at="2026-09-21T10:00:00+00:00",
)
```

---

## Что сознательно не реализовано в первой версии — и почему

SC05 («Source + Evidence Graph») в карте портфеля описывает полноценный
граф: claim ↔ физический источник ↔ evidence-регион ↔ конфликт ↔
коррекция. Текущая версия `EvidenceStore` — плоский append-only лог
`EvidenceLink`, не граф: нет отдельных узлов для source/claim с
собственной идентичностью, нет явного объекта `ConflictState` со своим
жизненным циклом (обнаружен → рассмотрен → разрешён), нет `correction`
(когда преподаватель или новая версия корпуса меняет ранее записанный
entailment).

Как и в B3 (см. «Rubric — как пользоваться.md», раздел про
multi-rater/adjudication) — при разборе портфеля не нашлось ни одного
описанного бота с реализованным graph-слоем поверх этой идеи, только сама
идея (SC04/SC05 в реестре помечены `не извлечён`, deep 1/3 и 2/3
соответственно — самое слабое подтверждение из модулей B1–B5). `claim`
хранится как сырой текст, а не как отдельная сущность со своим id — это
достаточно для `get_for_claim()`/`get_conflicting()` уже сейчас, но не
позволяет отследить, например, что два по-разному сформулированных claim
на самом деле об одном и том же факте.

Схема (`claim` как текст, `chunk_ref`/`source` отдельно от самого текста
чанка) уже содержит место под расширение — когда появится конкретный бот с
описанным сценарием конфликта источников или ревизии corpus, `ConflictState`
и correction-записи можно добавить без ломающих изменений интерфейса.

---

## Инварианты

- `EvidenceChecker.verify()` — stateless, без side effects: не пишет в
  `EvidenceStore` сама (в отличие от `record_rating()` в B3, которая сразу
  и создаёт запись, и является единственным способом её создать).
- `EvidenceStore.record()` — append-only, как `Attempt` в B2 и
  `RaterRecord` в B3: повторная проверка того же `claim`/`chunk_ref`
  (например, после обновления корпуса) добавляет новую `EvidenceLink`, не
  перезаписывает предыдущую — вся история проверок остаётся в БД.
- `entailment == "UNKNOWN"` — легитимный допустимый результат, не ошибка;
  неразборчивый ответ LLM-классификатора тоже становится `UNKNOWN`
  (fail-closed), не угадывается в сторону `SUPPORTED`.
- Навык не решает сам, доверять ли чанку — решение принимает
  `EvidenceChecker`, навык только реагирует на `entailment` уже принятого
  решения.
