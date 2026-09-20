# Simulation & Scenario Engine (C1): как пользоваться

Практическое руководство по `botkit.simulation` — коду в `src/botkit/simulation/`.
Про архитектурные решения, происхождение из SC02+SC03 и разбор портфеля см.
раздел C1 в [«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md);
здесь — как это реально вызывать.

---

## Общая схема — два независимых Protocol

C1 объединяет две смежные, но разные ответственности под одной группой:

```
SC02 — устойчивая роль с состоянием              SC03 — генерация кейсов с ключом ответа
──────────────────────────────────────           ──────────────────────────────────────
PersonaSimulator (base.py)                        ScenarioGenerator (base.py)
   │                                                  │
   ├── SQLiteSimulationLog        persistence         ├── SQLiteScenarioGenerator   persistence
   │    (sqlite_store.py)         без LLM              │    (sqlite_store.py)        без LLM
   │                                                    │
   ├── LLMPersonaSimulator        реальный LLM          ├── LLMScenarioGenerator     реальный LLM
   │    (persona.py)              A1 LLMProvider        │    (scenario.py)            A1 LLMProvider
   │                                                    │
   └── route_or_continue()        B1-интеграция          └── (нет B1-интеграции —
        (routing.py)              зеркало B2                  не диалоговый навык)
```

Оба используют **разные SQLite-файлы по соглашению**, не по техническому
требованию — ничто не запрещает передать один `db_path` обоим конструкторам
(разные таблицы, имена не пересекаются), но в `gb_edu_bot` и в демо-скриптах
это всегда `simulation.sqlite3` (SC02) и отдельный `scenario_*.sqlite3` (SC03)
— разное время жизни данных (диалоги vs учебные материалы с ключом ответа) и
разная чувствительность (ключ ответа не должен утечь через ту же БД, что и
обычные разговоры).

---

## Часть 1 — SC02: PersonaSimulator (устойчивая роль)

### Шаг 1 — создать persistence-слой

```python
from botkit.simulation.sqlite_store import SQLiteSimulationLog

# require_attempt=True когда-либо понадобится (FD42-020-подобный навык) —
# тогда нужен attempt_store; для свободного диалога (FD42-007-подобный,
# как oral_defense) можно не передавать вовсе:
simulation_log = SQLiteSimulationLog("logs/simulation.sqlite3")

# или, если этот же бот тестирует человеческий черновик через ту же роль:
simulation_log = SQLiteSimulationLog("logs/simulation.sqlite3", attempt_store)
```

Конструктор сам создаёт таблицы (`simulation_runs`, `simulation_turns`,
`execution_obstacles`), если их ещё нет.

### Шаг 2 — обернуть реальным LLM

```python
from botkit.simulation.persona import LLMPersonaSimulator, LLMRoleBoundaryClassifier

PERSONA_PROMPT = """
Ты — профессор Вернер Штрассер, придирчивый экзаменатор на устной защите...
[полный текст роли — предметная специфика бота, не часть фреймворка]
""".strip()

persona_simulator = LLMPersonaSimulator(
    llm,                              # LLMProvider (A1) — тот же клиент, что у остальных навыков
    simulation_log,
    PERSONA_PROMPT,
    role_classifier=LLMRoleBoundaryClassifier(llm),   # опционален, но рекомендован
)
```

`role_classifier` — второй, отдельный LLM-вызов **после** генерации ответа
персоны: проверяет, не сорвалась ли роль в совет/подсказку. Без него
`respond()` не фиксирует `advisory_content_present` вовсе — не скрытая
деградация, а явный выбор («не нужна эта проверка сейчас»).

### Шаг 3 — навык вызывает start()/respond()

```python
async def oral_defense(llm, query, user_id=None, persona_simulator=None, ...):
    if persona_simulator is None:
        return "Симулятор сейчас недоступен."

    actor_id = str(user_id or 0)
    task_ref = f"oral_defense::{actor_id}"

    run = await persona_simulator.start(
        actor_id, task_ref, simulator_policy_version=1, literalness_profile_version=1,
    )
    response = await persona_simulator.respond(run.run_id, query)
    return response
```

Навык **всегда** вызывает `start()` — продолжение уже идущего диалога
перехватывается раньше, в коде бота, через `route_or_continue()` (Шаг 4),
до того как `route()` вообще выбрал бы этот навык.

### Шаг 4 — интеграция с B1: route_or_continue()

Это ключевая деталь, без которой второе сообщение студента внутри диалога
классифицировалось бы `IntentRouter`'ом заново, как обычный вопрос:

```python
from botkit.simulation.routing import route_or_continue

# В коде бота, ДО вызова state.engine.route(text, ...):
if state.persona_simulator is not None:
    turn = await route_or_continue(
        state.persona_simulator, str(user_id), f"oral_defense::{user_id}", text,
    )
    if turn.outcome == "exited":
        await message.answer("Защита завершена.")
        return
    if turn.outcome == "continued":
        await message.answer(turn.response)
        return
    # turn.outcome == "not_active" — ничего не перехвачено, продолжаем как обычно:
    # intent = await state.engine.route(text, ...)
```

Три исхода:
- **`"not_active"`** — нет идущего run для этого `(actor_id, task_ref)`;
  вызывающий код продолжает свой обычный путь (B1 `route()`).
- **`"continued"`** — `text` ушёл в `respond()` того же run;
  `turn.response` уже готов к отправке студенту.
- **`"exited"`** — `text` совпал с одной из `exit_phrases`
  (по умолчанию `DEFAULT_EXIT_PHRASES = {"закончить", "завершить", "хватит",
  "стоп", "выйти", "выход", "/exit", "/stop", ...}`, настраиваемо через
  `exit_phrases=frozenset({...})`); `complete()` уже вызван.

Без этого шага `SimulationRun.status` никогда не меняется сам — студент,
однажды начав диалог, навсегда «застревал» бы в этом навыке: `get_active_run()`
находил бы активный run на каждом следующем сообщении, минуя остальные
навыки бота целиком.

### Шаг 5 — прочитать историю диалога

```python
turns = await persona_simulator.get_turns(run.run_id)
# [SimulationTurn(turn_index=0, actor_message="...", persona_response="..."), ...]
# root-first — от первой реплики до текущей, как get_lineage() в B2

obstacles = await simulation_log.get_obstacles(run.run_id)
# [ExecutionObstacle(advisory_content_present=True, evidence_ref="...", ...), ...]
# зафиксированные нарушения роли — обычно пустой список, если персона держится
```

`SimulationTurn` пишется **автоматически внутри `respond()`**, а не
навыком отдельным вызовом — так же, как `BotResponse` в B2 пишется внутри
`record_response()`. Без этого шага история диалога была бы восстановима
только из сырого текстового лога процесса, не структурированно.

---

## Часть 2 — SC03: ScenarioGenerator (генерация кейсов с ключом)

### Шаг 1 — создать persistence-слой

```python
from botkit.simulation.sqlite_store import SQLiteScenarioGenerator

scenario_store = SQLiteScenarioGenerator("logs/scenario.sqlite3")
```

### Шаг 2 — обернуть реальным LLM

```python
from botkit.simulation.scenario import LLMScenarioGenerator

SOURCE_STANDARD_PROMPT = """
Ты — эксперт по ветеринарной паразитологии...
Эталонный жизненный цикл (source standard):
1. ...
""".strip()

generator = LLMScenarioGenerator(
    llm, scenario_store, SOURCE_STANDARD_PROMPT,
    model_config_version=llm.model_name,
)
```

`source_standard_prompt` — единственный вход, на основе которого модель
решает, что верно и что нет. Это не проверенный внешний источник истины —
просто текст, переданный при создании генератора; корректность самого
эталона остаётся на человеке (см. «Что НЕ гарантирует» ниже).

### Шаг 3 — сгенерировать набор

```python
defect_set = await generator.generate_defect_set(
    task_ref="fasciola-1",
    source_standard_ref="fasciola_lifecycle_v1",   # непрозрачный id версии эталона, не сам текст
    intended_defect_count=4,
)

defect_set.guard_status        # "draft" — сразу после генерации
defect_set.stimulus_text       # НОВЫЙ текст с внедрёнными ошибками — то, что увидит студент
defect_set.defects             # [{"defect_id": "defect-0", "defect_type": "...", "locator": "...", "description": "..."}, ...]
```

**Промпт явно запрещает рецензию на эталон.** Первая версия реализации
допустила ровно эту ошибку на живом прогоне: попросили «найди ошибки в
эталоне» — модель сравнила эталон сам с собой вместо того, чтобы написать
новый текст и самой его испортить. Промпт в `LLMScenarioGenerator` прямо
формулирует задачу как «напиши новый текст... это не рецензия и не
сравнение», и парсинг требует непустого `stimulus_text` отдельно от
`defects` — `DefectGenerationParseError`, если модель вернула только список
без самого текста.

**`defect_id` присваивается стором, не моделью** — `SQLiteScenarioGenerator`
проставляет `defect-0`, `defect-1`, ... по позиции при записи, перезаписывая
любое поле `defect_id`, которое LLM могла случайно вернуть сама. Без этого
не было бы устойчивого способа сверить ответ студента с реальным ключом.

### Шаг 4 — guard-цикл: draft → validated → released

```python
from botkit.simulation.sqlite_store import GuardNotReleasedError

# Пока draft — record_diagnosis() отклоняет попытку студента:
try:
    await scenario_store.record_diagnosis(defect_set.defect_set_id, "student-1", [])
except GuardNotReleasedError:
    ...  # ожидаемо: набор ещё не проверен человеком

# Явный переход — предполагает, что домен-эксперт прочитал stimulus_text и
# defects, и подтвердил: все N ошибок реально там, лишних нет (R0102/R0206 FD42-006):
await scenario_store.validate_defect_set(defect_set.defect_set_id)
await scenario_store.release_defect_set(defect_set.defect_set_id)
```

**Это два отдельных ручных действия, не автоматический побочный эффект
генерации.** Ничто в коде физически не проверяет, что между генерацией и
`release` произошёл реальный человеческий просмотр — методы просто меняют
статус. Гарантия человеческого review — это ответственность того, как
написан код навыка вокруг этих методов (см. «Что НЕ гарантирует» ниже), а
не самого стора.

### Шаг 5 — студент присылает ответ

```python
diagnosis = await generator.record_diagnosis(
    defect_set_ref=defect_set.defect_set_id,
    actor_id="student-1",
    found_items=["defect-0", "defect-2", "defect-99"],  # что студент назвал — включая возможные ошибки
    correction_prompt_ref=None,   # ссылка на WC05 "teach-back" промпт студента, если применимо
)
```

### Шаг 6 — сверить ответ с ключом

```python
score = await generator.check_diagnosis(diagnosis.diagnosis_id)

score.correct_defect_ids      # ["defect-0", "defect-2"]  — реально найденные
score.missed_defect_ids       # ["defect-1", "defect-3"]  — пропущенные
score.false_positive_items    # ["defect-99"]              — выдуманные/неверные метки
```

`check_diagnosis()` **не вызывается автоматически** внутри `record_diagnosis()`
— решение раскрыть результат остаётся у навыка (можно показать сразу, можно
только после N попыток, можно вообще скрыть от студента и показать только
преподавателю).

---

## Что НЕ гарантирует эта реализация — и почему

**Научную корректность эталона.** `source_standard_prompt`/`source_standard_ref`
— это то, что передал вызывающий код, без проверки, что сам эталон верен.
Модель одновременно и «автор» текста, и «автор» списка того, что она сама
там исказила — независимого источника истины в цикле нет. T006-01 («ground
truth binding») требует резолвить каждый дефект к принятому эталону — это
ответственность `validate_defect_set()`, но сама проверка происходит вне
кода, глазами человека.

**Что `validate_defect_set()`/`release_defect_set()` вызвал реальный
человек, а не скрипт.** Методы меняют статус по факту вызова — не проверяют,
кто вызвал. Правильная форма навыка — разделить это на два разных действия
бота: «показать преподавателю черновик» (`generate_defect_set()`, ответ с
`guard_status=draft`) и отдельную команду вроде `/approve_case`, которую
может вызвать только преподаватель после явного прочтения. Ни то, ни другое
не реализовано как готовый UI-компонент фреймворка — это делает конкретный
бот.


---

## Инварианты

- `PersonaSimulator.start()` без `require_attempt=True` не требует
  `attempt_store` вовсе — `actor_id` прямое поле `SimulationRun`, не
  выводится через `attempt_ref` (который поэтому не обязателен).
- `respond()` пишет `SimulationTurn` автоматически, до классификации роли —
  история диалога восстановима независимо от того, использует ли навык
  `role_classifier`.
- `advisory_content_present=True` — структурный маркер нарушения роли, не
  стилистическая деталь; `None` (классификатор не уверен) трактуется как
  потенциальное нарушение (fail-closed), не как чистый ответ — в отличие от
  B2, где неуверенность ведёт к вопросу студенту, здесь вопрос в диалоге не
  задашь.
- `route_or_continue()` — единственная точка, где бот решает «продолжение
  или новое сообщение»; B1 `IntentRouter` не участвует в этом решении вовсе.
- `intended_defect_count` обязан совпасть с фактическим `len(defects)` до
  записи — `DefectCountMismatchError`, а не тихая правка счётчика.
- `guard_status` — строго `draft → validated → released`, без пропуска
  шагов; `record_diagnosis()` отклоняет всё, что не `released`.
- `check_diagnosis()` — чистая сверка множеств, не переоценивает и не
  изменяет `StudentDiagnosis`; можно вызывать многократно без побочных
  эффектов.
