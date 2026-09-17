# Attempt & Revision Store (B2): как пользоваться

Практическое руководство по `botkit.attempts` — коду в `src/botkit/attempts/`.
Про архитектурные решения и инварианты см. раздел B2 в
[«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md);
здесь — как это реально вызывать. Живой пример подключения к реальному
навыку — `feedback`/`text_analysis` в `gb_edu_bot/app/skills.py` и
`gb_edu_bot/app/bot_vk.py`.

---

## Общая схема

```
SQLiteAttemptStore(db_path)        (botkit.attempts.sqlite_store)
   │  — один файл на диске, переживает рестарт процесса
   ▼
record_revision(store, actor_id, task_ref, content)   (botkit.attempts.base)
   │  — сам находит latest() для (actor_id, task_ref) и подставляет
   │    его как parent_attempt_id — навык не вызывает latest() вручную
   ▼
Attempt(attempt_id, parent_attempt_id, content, origin="HUMAN", ...)
   │
   ├── record_response(attempt_id, content, ...)   → BotResponse (строго 1:1)
   ├── get_lineage(attempt_id)                     → [Attempt, ...] от корня
   ├── attempt_count(store, actor_id, task_ref)     → int, длина цепочки
   │
   └── ActiveSessionResolver(store, classifier=...)   (botkit.attempts.sessions)
          │  — только для навыков со СВОБОДНОЙ сменой работы:
          │    решает через LLM, это правка или новая, несвязанная работа
          ▼
       SessionResolution(task_ref, question_kind)
```

Два независимых слоя: `record_revision`/`get_lineage`/`attempt_count`
работают с уже известным `task_ref` — это ядро B2, нужно почти всегда.
`ActiveSessionResolver` — надстройка для одного конкретного сценария (см.
«Два режима» ниже), подключается не всегда.

---

## Шаг 1 — создать хранилище

```python
from botkit.attempts.sqlite_store import SQLiteAttemptStore

attempt_store = SQLiteAttemptStore("logs/attempts.sqlite3")
```

Один раз при старте бота, как один `ChromaVectorStore` на коллекцию в A3.
Конструктор сам создаёт файл и таблицы (`attempts`, `bot_responses`), если
их ещё нет — вызывать отдельный `init()`/`migrate()` не нужно.

---

## Шаг 2 — записать попытку студента

```python
from botkit.attempts.base import record_revision

attempt = await record_revision(
    attempt_store,
    actor_id=str(user_id),
    task_ref="feedback",     # см. "Что такое task_ref" ниже
    content=query,           # сам текст попытки студента
)
```

`record_revision()` — не метод `AttemptStore`, а свободная функция поверх
него (как `search_across()` над `VectorStore` в A3): сама вызывает
`store.latest(actor_id, task_ref)` и подставляет найденный `attempt_id` как
`parent_attempt_id`. Если попыток ещё не было — `parent_attempt_id=None`
(корень цепочки), без ошибки.

Вызывать **до** обращения к LLM — попытка студента переживает даже сбой
генерации ответа, а не только успешный путь.

### Что такое `task_ref`

Непрозрачная строка, отвечающая на вопрос «над какой именно работой сейчас
попытка» — не то же самое, что имя навыка. `AttemptStore` группирует
попытки строго по паре `(actor_id, task_ref)`: одинаковый `task_ref` =
одна и та же работа (ревизии свяжутся в цепочку), разный `task_ref` = не
связанные работы, даже у одного и того же студента и в одном навыке.

Разные навыки, которые пишут в один и тот же `attempt_store`, физически
делят одну таблицу на диске, но никогда не видят историю друг друга, если
используют разные `task_ref` — конфликтов не возникает без специальной
изоляции.

---

## Шаг 3 — записать ответ бота на эту попытку

```python
response = await llm.ainvoke(prompt)

await attempt_store.record_response(
    attempt.attempt_id,
    response,
    skill_context="FEEDBACK",              # какой навык сформировал ответ
    support_policy_ref=FEEDBACK_POLICY.policy_id,   # какая SupportPolicy (B1) применялась
    llm_response_ref=None,                 # ссылка на UsageEvent (A2), если нужна привязка к usage-логу
)
```

Строго 1:1 с `Attempt` — повторный вызов `record_response()` на тот же
`attempt_id` бросает `DuplicateResponseError`, а не перезаписывает и не
заводит второй ответ (гарантия на уровне схемы БД — `attempt_id` в таблице
`bot_responses` объявлен `UNIQUE`). Ретраи LLM (A1) разрешаются внутри
генерации ответа, до этого вызова — `record_response()` вызывается один раз
с уже финальным содержимым.

```python
from botkit.attempts.sqlite_store import DuplicateResponseError

try:
    await attempt_store.record_response(attempt.attempt_id, response)
except DuplicateResponseError:
    ...  # уже был записан ответ на этот attempt — логическая ошибка вызывающего кода
```

---

## Шаг 4 — прочитать историю

```python
lineage = await attempt_store.get_lineage(attempt.attempt_id)
# [Attempt(v1), Attempt(v2), Attempt(v3)] — root-first, от первой попытки до текущей

latest = await attempt_store.latest(str(user_id), "feedback")
# последняя попытка этого actor_id по этому task_ref, или None

count = await attempt_count(attempt_store, str(user_id), "feedback")
# длина цепочки — то же самое, что len(get_lineage(...)), но без attempt_id на входе
```

`attempt_count()` — свободная функция (`botkit.attempts.base`), нужна в
основном для лимита попыток (см. Режим 2 ниже) — до записи, не после.

---

## Два режима подключения — выбор навыка, не флаг внутри B2

Вопрос «к какой работе относится это сообщение» решается по-разному в
разных навыках, поэтому это архитектурная развилка на уровне вызывающего
кода, а не переключатель внутри `AttemptStore`.

### Режим 1 — свободная смена работы

Студент сам решает, продолжает прежнюю работу или начинает новую, никак
явно об этом не сообщая (пример: `text_analysis`/`feedback` в `gb_edu_bot`
— студент присылает то правку черновика, то совсем другой текст). Здесь
нужен `ActiveSessionResolver`:

```python
from botkit.attempts.sessions import ActiveSessionResolver, LLMRevisionClassifier

session_resolver = ActiveSessionResolver(
    attempt_store,
    classifier=LLMRevisionClassifier(llm),   # переиспользует тот же LLMProvider, что и навыки
)

result = await session_resolver.resolve(str(user_id), "feedback", text)
if result.task_ref is None:
    # result.question_kind — "ask_continue_or_new" или "reprompt_ambiguous"
    await send(SESSION_QUESTIONS[result.question_kind])
    return  # ждём ответа студента следующим сообщением
task_ref = result.task_ref

attempt = await record_revision(attempt_store, str(user_id), task_ref, text)
```

Как это решается внутри `resolve()`:
1. Первое сообщение этого `actor_id` в этом навыке вообще — `task_ref`
   создаётся сразу, без вопроса (продолжать пока нечего).
2. Есть активная работа — `classifier.is_revision(previous_text, new_text)`
   сравнивает новый текст с последней попыткой: `True` → тихо продолжаем
   без вопроса; `False` или `None` (не уверен) → студенту задаётся явный
   да/нет-вопрос («продолжаем прежнюю работу или это новая?»).
3. Ответ на вопрос разбирается структурно (`classify_session_answer`), не
   через LLM — набор слов да/нет настраиваемый параметр
   (`SessionWords`), по умолчанию — русский (`DEFAULT_RU_SESSION_WORDS`).
   Неоднозначный ответ → переспрос (`question_kind="reprompt_ambiguous"`),
   а не угадывание.

Выбор сессии переживает рестарт процесса — резолвер хранит его в самом
`attempt_store` (служебный `task_ref` вида `"{skill_name}__active_session"`),
а не только в памяти. In-memory состояние «кто сейчас ждёт ответа» рестарт
не переживает — это осознанный компромисс: студент, чей вопрос был задан,
но не отвечен, получит его заново при следующем сообщении, вместо потери
прогресса.

Без `classifier` (`ActiveSessionResolver(store)`, параметр по умолчанию
`None`) резолвер не пытается угадывать — сразу переходит к вопросу на
любом сообщении, кроме самого первого. Рабочее, но грубое поведение —
полезно, если LLM ещё не подключён к сессиям вовсе.

### Режим 2 — фиксированная работа, без права смены

Экзамен/тест с заранее известным и ограниченным числом попыток, где
студент не может «начать заново» просто прислав другой текст. Здесь
`ActiveSessionResolver` не подключается вовсе — сама постановка вопроса
«это та же работа?» неприменима, потому что работа всегда одна:

```python
MAX_ATTEMPTS = 3
task_ref = f"exam-{exam_id}"   # известен заранее, не резолвится из текста

if await attempt_count(attempt_store, str(user_id), task_ref) >= MAX_ATTEMPTS:
    return "Лимит попыток исчерпан"

attempt = await record_revision(attempt_store, str(user_id), task_ref, answer_text)
```

`attempt_count()` вызывается **до** `record_revision()` — превышение
лимита не должно создавать (N+1)-ю попытку молча, а должно отказывать до
записи.

Третий, ещё не реализованный вариант — гибрид (свободная смена темы, но не
более N раз) — не входит в текущую версию B2.

---

## Финальные `Attempt`/`BotResponse` — что реально внутри

```python
Attempt(
    attempt_id="a1b2c3...",
    actor_id="42",
    task_ref="feedback",
    content="черновик студента",
    content_hash="52a14e0d...",        # sha256 от content
    created_at=datetime(...),
    origin="HUMAN",                     # неизменяемый маркер, machine-diff не подменяет
    parent_attempt_id=None,             # или attempt_id предыдущей ревизии
)

BotResponse(
    response_id="d4e5f6...",
    attempt_id="a1b2c3...",             # FK, строго 1:1
    content="ответ бота на этот attempt",
    created_at=datetime(...),
    skill_context="FEEDBACK",
    support_policy_ref="feedback_v1",
    llm_response_ref=None,              # ссылка на A2 UsageEvent, не дублирует токены/cost здесь
)
```

---

## Инварианты

- `record()`/`record_revision()` никогда не перезаписывают существующий
  `Attempt` — только добавляют новый с `parent_attempt_id`. Хранилище по
  умолчанию персистентно (SQLite), не in-memory dict.
- `record_response()` — строго один `BotResponse` на один `attempt_id`:
  повторный вызов бросает `DuplicateResponseError`.
- `task_ref` — единственное, что определяет «одну и ту же работу»;
  `record_revision()` сам не решает, что считать той же темой — это
  ответственность вызывающего навыка (см. Режим 1/2 выше).
- `ActiveSessionResolver` — надстройка для Режима 1, не часть ядра B2;
  подключать его для Режима 2 (фиксированный `task_ref`) не нужно и
  архитектурно неверно.
- `attempt_count()` для лимита попыток вызывается до записи, не после.
- `LLMRevisionClassifier.is_revision()` возвращающий `None` («не уверен»)
  и не удавшийся парсинг ответа LLM обрабатываются одинаково — fallback на
  явный вопрос студенту, никогда не молчаливое угадывание.
