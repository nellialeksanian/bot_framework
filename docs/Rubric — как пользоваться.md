# Rubric / Rater Service (B3): как пользоваться

Практическое руководство по `botkit.rubric` — коду в `src/botkit/rubric/`.
Про архитектурные решения, происхождение из SC08 и разбор портфеля см.
раздел B3 в [«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md);
здесь — как это реально вызывать. Живой пример подключения к реальному
навыку — `feedback` в `gb_edu_bot/app/skills.py` и `gb_edu_bot/app/bot_vk.py`.

---

## Общая схема

```
SQLiteRubricStore(db_path)          (botkit.rubric.sqlite_store)
   │  — один файл на диске, переживает рестарт процесса
   ▼
sync_package(store, domain, dimensions)   (botkit.rubric.base)
   │  — сравнивает dimensions (код) с активным CriterionPackage (БД):
   │    нет пакета -> регистрирует; совпадает -> ничего не делает;
   │    отличается -> новая версия, старая помечается "superseded"
   ▼
CriterionPackage(package_id, domain, version, dimensions, owner_status)
   │
   ├── render_criteria_block(package)     → текст критериев для промпта
   │
   └── record_rating(target_attempt_id, package_ref, ..., labels_found)
          ▼
       RaterRecord(rater_record_id, target_attempt_id, package_ref, ...)
          │
          └── get_ratings(target_attempt_id)   → [RaterRecord, ...]
```

`target_attempt_id` — это `Attempt.attempt_id` из B2: B3 не хранит сам
текст попытки студента (это ответственность `AttemptStore`), только оценку
этой попытки по критериям. Два стора, две БД, связаны по смыслу через этот
id, не через FK на уровне SQLite (`SQLiteAttemptStore` и `SQLiteRubricStore`
— независимые файлы).

---

## Шаг 1 — создать хранилище

```python
from botkit.rubric.sqlite_store import SQLiteRubricStore

rubric_store = SQLiteRubricStore("logs/rubric.sqlite3")
```

Один раз при старте бота, как `SQLiteAttemptStore` в B2. Конструктор сам
создаёт файл и таблицы (`criterion_packages`, `rater_records`), если их
ещё нет.

---

## Шаг 2 — определить критерии и синхронизировать пакет

```python
from botkit.rubric.base import Criterion, sync_package

FEEDBACK_CRITERIA = [
    Criterion(
        dimension_id="logical_gaps",          # стабильный id — то, что модель вернёт в labels_found
        label="Логические разрывы в алгоритме анализа",
        anchor_examples=[
            "вывод сделан без промежуточных шагов декомпозиции/абстракции",
            "тезис и аргумент не разграничены",
        ],
    ),
    Criterion(
        dimension_id="source_grounding",
        label="Опора на источники",
        anchor_examples=["утверждение сделано без ссылки на страницу/главу курса"],
    ),
]

active_package = await sync_package(rubric_store, "feedback_domain", FEEDBACK_CRITERIA)
```

Критерии живут как обычная Python-константа рядом с навыком — как
`SupportPolicy`/`deny_patterns` в B1, не в отдельном админ-хранилище.
`sync_package()` вызывается на каждом старте процесса:

- пакета для этого `domain` ещё нет → регистрирует и активирует первую версию;
- активный пакет уже совпадает с `FEEDBACK_CRITERIA` по содержимому
  (`dimension_id`/`label`/`anchor_examples`) → ничего не делает, версия не растёт;
- кто-то поменял формулировку критерия или список критериев в коде →
  регистрирует **новую** версию и активирует её; предыдущая версия не
  удаляется, помечается `"superseded"` — старые `RaterRecord`, ссылающиеся
  на неё через `package_ref`, остаются валидными для истории.

Редактирование критериев = правка списка `Criterion` в коде + обычный
рестарт бота. Никакого отдельного скрипта или ручного вызова
`register_package()`/`activate_package()` не требуется — `sync_package()`
сам решает, нужна ли новая версия.

```python
from botkit.rubric.base import render_criteria_block

criteria_block = render_criteria_block(active_package)
prompt = f"""
...
{criteria_block}
...
Ответь СТРОГО в формате JSON:
{{"response": "...", "labels_found": ["dimension_id1", "dimension_id2"]}}
"""
```

`render_criteria_block()` — чистая функция (как `render_support_block()` в
B1): без LLM-вызова и side effects, рендерит `dimensions` пакета в текст.
Промпт должен просить модель возвращать `dimension_id`, не `label` —
`label` можно переформулировать при следующей версии пакета, `dimension_id`
остаётся стабильным идентификатором.

---

## Шаг 3 — записать оценку

```python
response = await llm.ainvoke(prompt)
parsed = json.loads(response)

if parsed.get("labels_found"):
    await rubric_store.record_rating(
        target_attempt_id=attempt.attempt_id,   # тот же Attempt, что в B2
        package_ref=active_package.package_id,
        criterion_version=active_package.version,
        rater_id=model_name,                    # кто оценивал
        labels_found=parsed["labels_found"],
        evidence=[],                            # цитаты/локаторы, если нужны
        rater_qualification="llm",               # напр. "llm", "teacher"
    )
```

Append-only, как `Attempt`/`BotResponse` в B2 — `record_rating()` никогда
не перезаписывает существующую запись, только добавляет новую.
`package_ref` обязан ссылаться на уже зарегистрированный пакет —
`record_rating()` с неизвестным `package_ref` бросает `UnknownPackageError`.

---

## Шаг 4 — прочитать оценки

```python
ratings = await rubric_store.get_ratings(attempt.attempt_id)
# [RaterRecord, ...] — все записанные оценки этой попытки (сейчас — не более одной, см. ниже)

active = await rubric_store.get_active_package("feedback_domain")
# бросает NoActivePackageError, если ни один пакет ещё не активирован
```

---

## Что сознательно не реализовано в первой версии — и почему

Первичный контракт (LEDGER v1.4, FD42-023/028/031) требует не одну оценку,
а **множественных независимых рейтеров** с явным разрешением разногласия:
state machine `DRAFT → ACCEPTED → ACTIVE`, затем независимые `RATED`-записи,
конфликт → `DISAGREEMENT`, разрешение → отдельная запись `ADJUDICATED`
(никогда не переписывающая исходные оценки). Deny paths: тихое усреднение
конфликтующих оценок, модель как единственный gold-эталон.

Этот механизм **не реализован здесь** — при целевом разборе портфеля
(FD42-023/028/031, ПОРТФОЛЬ v2.2, P039) не нашлось ни одного описанного
примера, где было бы явно указано, кто именно два независимых рейтера и
как оценка второго (обычно подразумевается человек) технически попадает в
систему бота — только абстрактная ролевая модель (owner/rater/adjudicator)
без описания канала ввода. Единственный конкретный паттерн — SHADOW/ASSISTED
режим у Овсянниковой (FD42-028): модель готовит кандидата, преподаватель
решает, что показать студенту — это ближе к B4 (`DecisionEvent`,
ACCEPT/EDIT/REJECT), чем к multi-rater adjudication.

Важно: **вторая LLM как "независимый" рейтер — явно запрещённый путь**
(deny path "model-authored gold"), не просто нереализованный случай — два
вызова одного класса источника не создают настоящей независимости.

Схема данных (`rater_id`, `rater_qualification`, `criterion_version`
отдельно от `package_ref`) уже содержит место под это расширение — когда
появится реальный бот с описанным multi-rater сценарием, `DISAGREEMENT`/
`ADJUDICATED` можно добавить без ломающих изменений интерфейса.

Также отложен `aggregate()` (дашборд мониторинга для преподавателя,
`TaxonomyDistribution`/`TimeRange`) — оба пока заглушки в `base.py`, без
конкретных требований (разрезы, период) реализовывать нечего.

---

## Финальные `CriterionPackage`/`RaterRecord` — что реально внутри

```python
CriterionPackage(
    package_id="40f4d694-...",
    domain="gb_edu_bot.feedback.pedagogical_text_analysis",
    version=1,
    dimensions=[
        Criterion(dimension_id="logical_gaps", label="...", anchor_examples=[...]),
        ...
    ],
    owner_status="active",       # "draft" | "accepted" | "active" | "superseded"
)

RaterRecord(
    rater_record_id="346a98dd-...",
    target_attempt_id="4f7fd8ff-...",   # Attempt.attempt_id из B2
    package_ref="40f4d694-...",
    criterion_version=1,
    rater_id="gpt-4o",
    rater_qualification="llm",
    labels_found=["logical_gaps", "source_grounding"],
    evidence=[],
)
```

---

## Инварианты

- `register_package()` никогда не изменяет уже зарегистрированный
  `CriterionPackage` — только добавляет новую версию (`version + 1` в
  рамках `domain`). Новый пакет по умолчанию `"draft"`, не виден
  `get_active_package()`, пока не активирован.
- `activate_package()` держит ровно один активный пакет на `domain` —
  активация новой версии автоматически переводит предыдущую активную в
  `"superseded"`, не удаляя её.
- `sync_package()` — рекомендуемая точка входа при старте бота вместо
  ручного `register_package()`/`activate_package()`: сравнивает код с БД
  по содержимому, версионирует только при реальном изменении критериев.
- `record_rating()` — append-only, как `Attempt` в B2: повторные оценки той
  же попытки добавляются, не перезаписывают предыдущие.
- Навык не изобретает названия меток текстом в промпте — берёт `dimension_id`
  из `CriterionPackage.dimensions`, привязанного к `domain`.
- Multi-rater/adjudication и вторая LLM как независимый рейтер — вне
  текущей версии; второе — явный deny path, не просто нереализованная фича.
