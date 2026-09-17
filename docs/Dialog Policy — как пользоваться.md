# Dialogue Policy Engine (B1): как пользоваться

Практическое руководство по `botkit.dialog_policy` — коду в
`src/botkit/dialog_policy/`. Про архитектурные решения, происхождение
интерфейса из SC01+SC06 и разбор на живом ТЗ (ФинМыш) см.
[«B1 — Dialogue Policy Engine — assumption doc.md»](B1%20—%20Dialogue%20Policy%20Engine%20—%20assumption%20doc.md);
здесь — как это реально вызывать.

---

## Общая схема

```
register_skill(name, handler, support_policy, description)   ×N вызовов
   │  — один раз при старте бота, для каждого навыка
   ▼
DefaultDialoguePolicyEngine  (хранит policy + description в памяти)
   │
   ├── .route(query, history, anchor)
   │      │
   │      ▼
   │   IntentRouter.classify(query, history, anchor, available_skills)
   │      │  — available_skills собран движком САМ из уже
   │      │    зарегистрированных навыков, не передаётся вручную
   │      ▼
   │   Intent(skill, confidence, fallback, fallback_reason)
   │
   ├── .check_attempt_gate(skill, actor_id, task_ref, attempts)   (CP11A)
   │      │  — структурная проверка через AttemptStore (B2)
   │      ▼
   │   bool — False блокирует вызов навыка
   │
   └── .render_support_block(skill, turn_index)   (CP11B)
          │  — чистая функция, без LLM-вызова и side effects
          ▼
       str — текст с deny_patterns + allowed_action_classes (+ fading),
             вставляется вызывающим кодом в промпт навыка
```

---

## Шаг 1 — определить `SupportPolicy` рядом с навыком

`SupportPolicy` не хранится в отдельном реестре (в отличие от
`CriterionPackage` в B3) — она живёт в том же файле, что и функция навыка,
потому что `deny_patterns` специфичны для одного конкретного навыка.

```python
from botkit.dialog_policy.base import SupportPolicy

TEXT_ANALYSIS_POLICY = SupportPolicy(
    policy_id="text_analysis_v1",
    version=1,
    target_action="самостоятельно проанализировать педагогический текст",
    protected_difficulty="выделение главного без готового вывода",
    attempt_gate=False,                       # CP11A: N/A для этого навыка
    allowed_action_classes=["question", "reflect"],
    deny_patterns=[
        "не пиши готовые тезисы/резюме/выводы за студента",
        "не выдумывай источники, которых нет в контексте курса",
    ],
    fading_rule=None,                          # см. "Дозирование по фазе" ниже
)
```

`target_action`/`protected_difficulty` — не рендерятся автоматически (в
отличие от `deny_patterns`), это данные для человека, читающего код, и
опционально можно вставить их в промпт вручную, как делает
`render_support_block()` для заголовка блока.

---

## Шаг 2 — собрать движок и зарегистрировать все навыки один раз

```python
from botkit.dialog_policy.engine import DefaultDialoguePolicyEngine
from botkit.dialog_policy.intent_router import DefaultIntentRouter

DOMAIN_NOTES = """
Reject (fallback) instead of picking a skill in these cases:

- fallback_reason="off_topic": the question is not related to pedagogy.
- fallback_reason="academic_dishonesty": the student asks you to write an
  essay/homework for them.
- fallback_reason="abuse": the message is an insult or overtly hostile.
""".strip()

def build_dialogue_policy_engine(llm):
    engine = DefaultDialoguePolicyEngine(
        intent_router=DefaultIntentRouter(llm, domain_notes=DOMAIN_NOTES)
    )

    engine.register_skill(
        "text_analysis",
        handler=text_analysis,
        support_policy=TEXT_ANALYSIS_POLICY,
        description=(
            "студент хочет разобраться в структуре педагогического текста, "
            "выделить главное или получить маршрут анализа."
        ),
    )
    # ... register_skill(...) для остальных навыков

    return engine
```

`description` заменяет текстовый список категорий, который раньше писался
вручную в промпте классификатора (`intent_recognition()`-стиль) — движок
сам собирает `SkillDescriptor` из всех регистраций и передаёт их в
`IntentRouter` при каждом `route()`. Добавить навык — значит вызвать
`register_skill()` ещё раз, не трогая ничего другого.

`domain_notes` — отдельный от `description` текст, задаёт критерии
**отказа**, а не выбора навыка (старая категория `IRRELEVANT`/`PLAGIARISM`
в `intent_recognition()`-стиле промптов). Каждая причина в тексте связана
с меткой `fallback_reason`, которую модель обязана вернуть — движок её не
придумывает сам, только парсит из ответа. Без `domain_notes` классификатор
судит только по описаниям навыков и не имеет явного сигнала, когда
запрос вообще не по домену бота — см. Шаг 3.

### `LLMProvider` — обёртка над реальным LLM-клиентом

`DefaultIntentRouter` ожидает `LLMProvider.ainvoke() -> LLMResponse` (A1).
Если ваш LLM-клиент (Coze/302.ai/другой) уже возвращает `str` напрямую —
нужен маленький адаптер:

```python
from botkit.llm.base import LLMResponse

class LegacyLLMAdapter:
    def __init__(self, legacy_llm):
        self._legacy_llm = legacy_llm
        self.model_name = getattr(legacy_llm, "model_name", "unknown")

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> LLMResponse:
        raw = await self._legacy_llm.ainvoke(prompt)
        text = raw if isinstance(raw, str) else str(raw)
        return LLMResponse(response=text, raw=text, parse_status="raw_text_fallback",
                            input_tokens=0, output_tokens=0)

    async def aclose(self) -> None:
        if hasattr(self._legacy_llm, "aclose"):
            await self._legacy_llm.aclose()
```

Пример такого адаптера — `_LegacyLLMAdapter` в `gb_edu_bot/app/skills.py`.
Когда A1 (LLM Provider Gateway) будет реализован в `botkit`, эта обёртка
станет не нужна — все LLM-клиенты будут сразу отдавать `LLMResponse`.

---

## Шаг 3 — классификация запроса

```python
intent = await engine.route(query, history=conversation_history, anchor=None)

FALLBACK_MESSAGES = {
    "academic_dishonesty": "Я не пишу готовые тексты за вас — давайте разберём материал вместе.",
    "abuse": "Пожалуйста, обращайтесь корректно.",
}
DEFAULT_FALLBACK_MESSAGE = "Ваш запрос выходит за пределы моей компетенции."

if intent.fallback:
    reply = FALLBACK_MESSAGES.get(intent.fallback_reason, DEFAULT_FALLBACK_MESSAGE)
    await send(reply)
else:
    handler = SKILL_HANDLERS[intent.skill]
    response = await handler(..., engine=engine)
```

`Intent.fallback` объединяет и явный отказ модели, и случай "не смогла
распарсить ответ", и выдуманное моделью имя навыка — всё, что не входит в
уже зарегистрированный список, превращается в `fallback=True`, а не в
исключение. `Intent.fallback_reason` (заполняется, только когда
`fallback=True`) различает *почему*: свободная snake_case-метка от модели
(`off_topic`, `academic_dishonesty`, ...) для явных случаев из
`domain_notes`, либо одна из служебных меток от самого движка —
`unparseable_response` (ответ не распарсился), `unknown_skill_name`
(модель вернула несуществующее имя навыка), `no_skills_registered`
(`route()` вызван до единого `register_skill()`). Без `fallback_reason`
все причины отказа сливались бы в одно универсальное сообщение — с ним
бот может ответить по-разному на "это не по теме" и на "я не пишу за вас
эссе", как показано выше.

### `SkillAnchor` — мягкая привязка к предыдущему навыку

```python
from botkit.dialog_policy.base import SkillAnchor

anchor = SkillAnchor(skill="refine_argument", last_question="Что доказывает этот тезис?")
intent = await engine.route(query, history=history, anchor=anchor)
```

Паттерн из `cicero_bot` (`last_skill_anchor`): передаётся, только если
предыдущий ход диалога уже определил активный навык — помогает
классификатору отличить продолжение той же темы от явного переключения.
Хранение `anchor` между сообщениями — ответственность вызывающего кода
(бота), не движка.

---

## Шаг 4 — attempt gate (CP11A)

```python
allowed = await engine.check_attempt_gate(
    skill="refine_argument", actor_id=str(user_id), task_ref="essay_v1",
    attempts=attempt_store,   # реализация AttemptStore (B2)
)
if not allowed:
    await send("Сначала зафиксируйте собственную попытку, потом я помогу её развить.")
    return
```

Если `support_policy.attempt_gate=False` — метод всегда возвращает `True`
без обращения к `attempts` (легитимный N/A, не заглушка). Если `True` —
структурно проверяет через `AttemptStore.latest(actor_id, task_ref)`, не
эвристикой и не текстом в промпте.

**На 2026-09-17 `AttemptStore` (B2) в `botkit` ещё не реализован** — только
`Protocol` в `attempts/base.py`. До его появления навыки с
`attempt_gate=True` физически нечем проверить; временно ставьте
`attempt_gate=False` с явным `TODO` (как сделано для `feedback` в
`gb_edu_bot`), а не оставляйте `True` без реальной проверки.

---

## Шаг 5 — support block (CP11B) внутри навыка

```python
async def text_analysis(llm, store, query, ..., engine=None, turn_index: int = 0) -> str:
    support_block = (
        engine.render_support_block("text_analysis", turn_index)
        if engine is not None
        else ""
    )

    prompt = f"""
    # Роль
    ...

    {support_block}

    # Остальные, не anti-solver ограничения (стиль, формат цитирования)
    ...
    """
    response = await llm.ainvoke(prompt)
    return response.response
```

`engine=None` по умолчанию — навык остаётся вызываемым и без движка
(полезно для юнит-тестов навыка в изоляции), просто `support_block` тогда
пустая строка.

**Что переносить в `deny_patterns`, а что оставлять текстом в промпте:**
только явные anti-solver запреты ("не решай за студента"). Правила стиля
("на Вы", без заголовков) и формат цитирования (`Source:`/`page:`) — это
RAG-механика и форматирование, остаются в тексте промпта навыка, не в
`SupportPolicy`. См. полный разбор всех пяти навыков `gb_edu_bot` в
assumption-документе, раздел про перенос ТЗ ФинМыш и живого кода.

### Дозирование по фазе (`FadingRule`)

```python
from botkit.dialog_policy.base import FadingRule

TEXT_ANALYSIS_POLICY = SupportPolicy(
    ...,
    fading_rule=FadingRule(
        levels=["full_support", "reduced", "no_support"],
        turns_per_level=1,
        level_descriptions={
            "full_support": "Дай развёрнутый маршрут анализа с примерами по каждому шагу.",
            "reduced": "Дай только краткое направление, без развёрнутых примеров.",
            "no_support": "Задай РОВНО ОДИН короткий вопрос и больше ничего.",
        },
    ),
)
```

`turn_index // turns_per_level` даёт позицию в `levels` (clamped на
последний уровень, не падает при выходе за границу). `level_descriptions`
обязателен для реального эффекта — само имя уровня (`"no_support"`) без
текстового описания ничего не меняет в ответе модели, это подтверждено
эмпирически на реальном LLM (302.ai): без описаний уровень — просто
неинформативный ярлык в промпте.

**Критично: `turn_index` не должен вычисляться из истории/активности
диалога студента.** Ledger v1.4 прямо называет это deny path
("постепенное ослабление запрета после повторного давления") — если
`turn_index` растёт от числа сообщений студента, студент может выманить
`no_support → full_support` откат просто настаивая. `turn_index` обязан
быть внешним входом (номер недели курса, номер задания) либо результатом
**отдельного** служебного LLM-вызова (не того, что отвечает студенту) —
полный разбор допустимого паттерна в assumption-документе, раздел про
инвариант из Ledger v1.4.

`FadingRule` определяется на том же месте, что и весь `SupportPolicy` —
рядом с навыком, не в отдельном реестре. В `gb_edu_bot` сейчас у всех пяти
навыков `fading_rule=None` (в исходном коде бота дозирования по фазе не
было).

---

## Финальный `Intent` — что реально внутри

```python
Intent(
    skill="feedback",             # имя, ровно как в register_skill()
    confidence=0.92,              # float | None — None, если модель не сообщила число
    fallback=False,               # True -> skill не входит в зарегистрированный список
    fallback_reason=None,         # str | None — заполнен, только если fallback=True
)
```

`fallback=True` соответствует и явному отказу модели (`FALLBACK_SKILL` от
`DefaultIntentRouter`), и случаю "распарсить ответ не удалось", и случаю
"модель выдумала несуществующее имя навыка" — вызывающему коду не нужно
различать причину, чтобы решить, вызывать ли навык. Но чтобы показать
разное сообщение отказа (см. Шаг 3) — есть `fallback_reason`, отдельное
поле, не смешанное с самим булевым флагом.

---

## Инварианты

- `deny_patterns` подставляется в промпт автоматически через
  `render_support_block()`, не копируется текстом в каждый новый навык —
  единая точка правки формулировок запрета для всего фреймворка.
- `check_attempt_gate` — структурная проверка через `AttemptStore`, не
  эвристика; `attempt_gate=False` — легитимное значение (N/A), а не
  недоработка политики.
- `render_support_block` — чистая функция: не вызывает LLM, не имеет side
  effects, детерминирована по `(skill, turn_index)`.
- `available_skills` для `IntentRouter` всегда собирается движком из уже
  зарегистрированных навыков — не дублируется вручную нигде ещё.
- `turn_index` — внешний вход, не производная от истории диалога/чата.
- `fallback_reason` заполняется только когда `fallback=True`; движок сам
  проставляет служебные метки (`unparseable_response`,
  `unknown_skill_name`, `no_skills_registered`), остальное — свободная
  snake_case-метка от модели, заданная через `domain_notes` автором бота.
