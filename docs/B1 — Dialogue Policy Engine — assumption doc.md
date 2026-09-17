# B1. Dialogue Policy Engine — временный документ для обращения к модели

Рабочий документ одного прохода: фиксирует решения, принятые при уточнении
интерфейса B1 (`«Модули фреймворка — приоритет.md», раздел B1`), прежде чем
переносить их в `src/botkit/dialog_policy/base.py`. Ассампшен: A1 (LLM Provider
Gateway) и A4 (Messenger Transport Adapter) уже реализованы и доступны — этот
документ их не описывает.

Живой пример, на котором проверялись решения: `dialogic_supportive_bot/app/skills.py`
(вне фреймворка, реальный production-бот) — `intent_recognition()` и промпты
`position_clarification`/`refine_argument`/`nuance_position`.

**Источники и их актуальность.** Проверено против двух поколений архитектурного
корпуса: (1) `bot_framework/docs/Реестр контрактов SC01-SC20.md` +
`00_CANON/*FROZEN*` в корне портфеля — та версия, на которую ссылается
приоритетный документ фреймворка; (2) `timur_data_2/SUCCESSOR_REFRESH_20260911/`
— более поздний, **терминально закрытый** (`PAIDEIA_FORMAL42_PHASE_CLOSED`,
2026-09-16) successor-корпус: Atlas v2.3, workbook v5.0, проходы P027–P056.
Расхождений в определении самого SC01/SC06 между поколениями не найдено — но
успешный корпус даёт более точную, специально сверенную финальную границу
ответственности (раздел 1.1 ниже), которой в старом реестре не было в явном
виде.

---

## 1.1. Успешный корпус (`timur_data_2`) — что изменилось, что нет

Проверка: не упростили ли мы SC01, ориентируясь только на старый реестр.
Ответ — почти нет, но одно уточнение было найдено и учтено.

**Каталог SC01–SC20 не изменился по составу** между поколениями — те же 20
компонентов, те же номера. Появился новый агрегирующий слой поверх них,
не заменяющий SC-каталог: **P033 "Shared Infrastructure Map"** группирует
SC-компоненты в 6 инфраструктурных bundle (IB01–IB06) по критерию
«что можно построить один раз и переиспользовать». Официальное, канонически
зафиксированное подтверждение состава B1:

> **IB01 INTERACTION + CHALLENGE RUNTIME = SC01 + SC02 + SC03 + SC06.**
> Reusable mechanics: dialogue/simulation/scenario/support-control engines и
> policy contracts. **Target action, persona truth, case truth, difficulty
> semantics, permitted hints and reveal rules remain local.**
> (`P033_SHARED_INFRASTRUCTURE_CLOSEOUT.md`)

Это прямо подтверждает, что SC01+SC06 в одном модуле — не упрощение
приоритетного документа фреймворка, а совпадает с официальной группировкой
источника (только шире — там ещё SC02/SC03, которые в приоритетном документе
осознанно вынесены в C1 как отдельная форма навыка).

**Дополнительно найден официальный failure mode**, подтверждающий, что SC01 и
SC06 обязаны идти вместе, а не быть независимыми: `P034`,
**FM05 — «dialogue policy without support-dose controller»**, зафиксирован как
архитектурный риск в 5 проектах портфеля. То есть наличие intent-роутинга без
anti-solver дозирования официально считается дефектом конструкции, а не
допустимым вариантом.

**Одно временное расхождение, которое не подтвердилось на терминальной
версии.** Промежуточный проход внутри Atlas v2.3 (не терминальный корпус, а
черновая проза внутри этого же документа) в какой-то момент предлагает более
широкий список: *«Следующий инженерный вопрос — не «сделать один Dialogue
Policy Engine на весь университет», а выделить минимальные policy primitives:
attempt gate, reveal level, hint state, support event, fading transition,
override, protected action»*. Это выглядело как потенциальное расширение
интерфейса на 5 дополнительных примитивов (`reveal_level`, `hint_state`,
`support_event`, `fading_transition`, `override`) сверх того, что было в этом
документе.

Проверка по терминально закрытому `P039` (`Приложение C. Shared components` —
самая поздняя, специально сверенная сводка) **не подтвердила это расширение**.
Итоговая формулировка границы ответственности там уже, а не шире:

- SC01: *«TargetAction, permitted hints **and question semantics** remain
  local»*
- SC06: *«ProtectedDifficulty **and reveal policy** remain local»*

То есть `hint`/`reveal` содержание терминально отнесено к тому же классу, что
`target_action`/`deny_patterns` — **данные, которые задаёт автор навыка**, а не
отдельные reusable-методы протокола движка (`hint_state()`, `reveal_level()`).
Промежуточный список из 7 примитивов был инженерной гипотезой одного прохода,
не пережившей терминальную сверку. Решение из раздела 2 ниже (CP11A/CP11B как
структурный gate + рендер текста) остаётся верным и после проверки более
позднего корпуса — расширять интерфейс дополнительными методами не требуется.

---

## 1. B1 — это два разных контракта, слитых по функции, не по происхождению

`«Модули фреймворка — приоритет.md»` объединяет SC01 и SC06 в один модуль B1,
потому что в реальных ботах intent-классификация и anti-solver дозирование
всегда стоят рядом в одном промпте. Но в реестре контрактов это два контракта
с разным статусом созревания:

| | SC01 (intent routing) | SC06 (support policy) |
|---|---|---|
| Статус в реестре | `DEMAND_CONFIRMED__CONTRACT_NOT_YET_EXTRACTED` — формальной схемы нет вообще | извлечён, `DS06 SupportPolicy + SupportEvent`, `SM06` state machine |
| Deep-подтверждение | 2/3, но именно как отдельная схема — 0 | 2/3 (FD42-028, FD42-031), family-specific, не universal substrate |
| Источник полей | нет — синтезированы из `cicero_bot` (`last_skill_anchor`) и из фактического кода `intent_recognition()` | `substrate v1.3`, раздел про P009: `SupportPolicy: policy_id/version, TargetAction, ProtectedDifficulty, AttemptGate, allowed_action_classes, RevealRules, SupportLevels, FadingRules, Override, TransferCondition` |

Следствие: `IntentRouter` в этом документе — инженерная конструкция по
аналогии с A1–A5 (выведена из кода, не из SC-каталога), а `SupportPolicy` —
контракт с прослеживаемым происхождением. Разная надёжность источника не
повод разносить их в разные модули фреймворка (мы это не делаем — B1 остаётся
одним модулем), но повод не приписывать `IntentRouter` тот же уровень
уверенности, что `SupportPolicy`.

---

## 2. CP11 распался на CP11A + CP11B — и это должно быть видно в интерфейсе

`substrate v1.3` (раздел P009, множественные упоминания начиная со строки про
«Особенно полезно сломался CP11») прямо фиксирует: старый композит
`first attempt + support dose + reveal/fading` оказался слишком широким и был
расщеплён надвое:

- **CP11A — protected human action / first attempt** (`FAMILY_3_OF_3_RELEVANT — 028, 031, 034`).
  Это **gate до вызова навыка**: нужна ли уже существующая человеческая попытка,
  прежде чем машина вообще вмешается. Применим только там, где machine act
  реагирует на уже существующий human artifact — substrate явно помечает
  `CP11A = N/A` для семей, где машинный артефакт предшествует попытке студента
  (deliberate-defect generation, DS09/C1). Для support-семейства (DS06) CP11A
  применим.
- **CP11B — support dose + reveal/fading** (`FAMILY_2_OF_2_RELEVANT — 028, 031`).
  Это **дозирование содержания внутри уже разрешённого вызова**: что конкретно
  можно сказать студенту, чего нельзя, как поддержка убывает по фазе.

Решение (подтверждено): интерфейс `DialoguePolicyEngine` должен явно
разделять эти два шага, а не сливать их в один метод — потому что источник
явно говорит, что это не всегда применимые вместе примитивы (CP11A может быть
`N/A`, пока CP11B — нет).

```
check_attempt_gate(...)   # CP11A — структурная проверка через B2/AttemptStore
render_support_block(...) # CP11B — чистая функция, текст для промпта
```

---

## 3. Что такое `SupportPolicy` конкретно — разбор на примере кода

`dialogic_supportive_bot/app/skills.py` не использует SupportPolicy — но
содержит ровно тот текст, который SupportPolicy должен заменить. Каждый навык
несёт свою секцию `## What You Must Not Do`, переписанную заново похожими, но
не идентичными словами:

- `position_clarification` (строки 160–165): «Do not argue against the
  student's position. Do not write a finished thesis on the student's behalf.»
- `refine_argument` (286–292): «Do not produce a ready-made list of arguments…
  Do not rewrite the student's thesis…»
- `nuance_position` (424–431): «Do not argue that the student's position is
  wrong… Do not push the student to abandon…»

Это три независимо написанных формулировки одного и того же запрета
(«не решай за студента»), с риском разъехаться при правке одного навыка и не
другого. `SupportPolicy.deny_patterns` — способ выразить это как данные один
раз и дать движку самому вставлять их в промпт, вместо того чтобы автор
навыка писал секцию текстом при каждом новом skill.

Пример, как эти три промпта выглядели бы через `SupportPolicy`:

```python
POSITION_CLARIFICATION_POLICY = SupportPolicy(
    policy_id="position_clarification_v1",
    version=1,
    target_action="сформулировать собственный тезис",
    protected_difficulty="переход от смутной идеи к чёткой формулировке позиции",
    attempt_gate=False,  # CP11A: не применимо — это первый контакт с идеей, попытки ещё нет
    allowed_action_classes=["reflect", "affirm", "question"],
    deny_patterns=[
        "не формулируй тезис за студента",
        "не спорь с позицией студента",
        "не предлагай альтернативный фреймворк как более верный",
    ],
    fading_rule=None,
)
```

---

## 4. Где физически живёт `SupportPolicy` — решение

Подтверждено: **рядом с определением skill, при регистрации** — не в
отдельном versioned PolicyStore (в отличие от B3/`RubricStore`, которому
центральное versioned-хранилище нужно, потому что критерии оценки должны
обновляться независимо от кода и разделяться между несколькими навыками).

Обоснование выбора (не из источника, инженерное решение по аналогии с
остальным документом): `deny_patterns` в `skills.py` всегда специфичны для
одного навыка — они формулируются в терминах его `target_action`
(«не формулируй тезис» относится только к `position_clarification`, «не
переписывай аргумент» — только к `refine_argument`). Централизованный реестр
оправдан, когда объект переиспользуется между навыками (как `CriterionPackage`
в B3 — один и тот же пакет критериев оценки нескольких разных попыток). Здесь
это не так: 13 из 15 ботов пишут deny-паттерны заново для каждого навыка,
потому что they are genuinely per-skill, не потому что нет общего хранилища.

Манифестация:

```python
router.register_skill(
    name="position_clarification",
    handler=position_clarification,
    support_policy=POSITION_CLARIFICATION_POLICY,
)
```

`DialoguePolicyEngine` при вызове навыка сам достаёт зарегистрированную
`SupportPolicy` и рендерит `render_support_block()` в текст, который
подставляется в промпт — автору навыка не нужно писать `## What You Must Not
Do` вручную.

`policies/` в текущем скелете (сейчас пустая, только `.gitkeep`) в этой схеме
не хранит `SupportPolicy` как основной путь — она предназначена для
опционального оверрайда (эксперимент/A-B тест той же policy без правки кода
навыка), не для обязательной регистрации.

---

## 5. Итоговый интерфейс

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from botkit.attempts.base import AttemptStore


@dataclass
class FadingRule:
    # как дозируется поддержка со временем/по фазе — CP11B
    levels: list[str]              # напр. ["full_support", "reduced", "no_support"]
    turns_per_level: int
    level_descriptions: dict[str, str] | None = None
    # что означает каждый уровень для модели — имя уровня само по себе
    # ("no_support") не самодостаточная инструкция; без описания модель
    # не меняет ответ, подтверждено эмпирически на реальном LLM (302.ai).


@dataclass
class SkillAnchor:
    # "мягкая привязка" к последнему активному навыку — паттерн cicero_bot
    # (last_skill_anchor: dict[chat_id -> {skill, question}])
    skill: str
    last_question: str


@dataclass
class SupportPolicy:
    policy_id: str
    version: int
    target_action: str                  # что должно остаться человеческим действием
    protected_difficulty: str           # какую трудность нельзя снимать за студента
    attempt_gate: bool                  # CP11A применим? нужен ли Attempt до вызова
    allowed_action_classes: list[str]   # question|hint|mark_error|reflect|affirm|...
    deny_patterns: list[str]            # CP11B: явные запреты
    fading_rule: FadingRule | None      # CP11B: дозирование по фазе


@dataclass
class Intent:
    skill: str
    confidence: float | None
    fallback: bool                      # сработал ли дефолт "если не уверен — выбирай X"
    fallback_reason: str | None = None
    # Почему сработал fallback — только когда fallback=True. Не закрытый
    # Literal: конкретные причины (off_topic, academic_dishonesty, ...) —
    # решение автора бота через domain_notes (см. раздел 7 ниже), framework
    # проставляет только служебные метки (unparseable_response и т.п.).


@dataclass
class SkillDescriptor:
    # Что IntentRouter видит о навыке при классификации — собирается
    # движком из уже зарегистрированных навыков, router остаётся stateless.
    name: str
    description: str


class IntentRouter(Protocol):
    async def classify(
        self,
        query: str,
        history: str,
        anchor: SkillAnchor | None,
        available_skills: list[SkillDescriptor],
    ) -> Intent:
        ...


SkillHandler = Callable[..., Awaitable[str]]


class DialoguePolicyEngine(Protocol):
    def register_skill(
        self,
        name: str,
        handler: SkillHandler,
        support_policy: SupportPolicy,
        description: str,
    ) -> None:
        ...

    async def check_attempt_gate(
        self, skill: str, actor_id: str, task_ref: str, attempts: AttemptStore
    ) -> bool:
        """CP11A. False -> вызов навыка блокируется до записи Attempt в B2.
        Если support_policy.attempt_gate=False, всегда возвращает True (N/A).
        AttemptStore передаётся явным параметром (не полем движка) — протокол
        остаётся stateless по отношению к B2, вызывающий код сам решает, какой
        конкретный store использовать, и это упрощает тестирование с fake."""
        ...

    def render_support_block(self, skill: str, turn_index: int) -> str:
        """CP11B. Чистая функция без внешних вызовов: deny_patterns +
        allowed_action_classes (+ применённый fading_rule по turn_index) -> текст
        для вставки в промпт навыка."""
        ...

    async def route(
        self, query: str, history: str, anchor: SkillAnchor | None
    ) -> Intent:
        """Собирает SkillDescriptor для всех зарегистрированных навыков
        (single source of truth — не дублируется в IntentRouter) и
        передаёт их в IntentRouter.classify()."""
        ...
```

**Инвариант (из приоритетного документа, подтверждён источником).**
`deny_patterns` подставляется в промпт автоматически движком через
`render_support_block()`, а не копипастится вручную в каждый новый навык —
единая точка правки формулировок запрета для всего фреймворка.

**Инвариант (из substrate, P009/P011).** `check_attempt_gate` — структурная
проверка через `AttemptStore` (B2), не эвристика и не текст в промпте.
`attempt_gate=False` — легитимное значение (N/A для семей, где CP11A не
применим), а не недоработка политики.

**Инвариант (из Ledger v1.4, критично для `turn_index`/`FadingRule`).**
Прямая цитата: *«Human authority: course/pedagogical owner определяет
TargetAction, допустимые QuestionType/HintLevel, first-attempt gate, reveal
и FadingPolicy. Модель может исполнять policy, но не сочинять её по ходу
сессии.»* Отдельно, в списке deny paths: *«постепенное ослабление запрета
после повторного давления»* — то есть **`turn_index`, растущий от числа
сообщений студента в чате или от числа вызовов навыка, — запрещённый
паттерн**, а не деталь реализации. Это ровно тот случай, когда студент мог
бы выманить `no_support → full_support` откат просто настаивая или
переспрашивая чуть другими словами (multi-turn bypass, ATF10).

`turn_index` обязан быть внешним входом, заданным вне диалога владельцем
курса/задания (например: номер недели курса, номер задания в программе,
явно выставленный этап — что угодно, что студент не может изменить самим
фактом переписки), а не производной от `ChatMemory`/истории сообщений.
Реализация `DefaultDialoguePolicyEngine.render_support_block(skill,
turn_index)` в этом смысле корректна — она ничего не знает о происхождении
`turn_index` и потому не диктует его источник, — но **любой вызывающий
код, который вычисляет `turn_index` из числа реплик студента, воспроизводит
запрещённый паттерн**, а не легитимную реализацию fading.

**Решение: фреймворк не типизирует критерий fading, преподаватель сам
решает, что передавать в `turn_index`.** Разбирали альтернативу — добавить
в `SupportPolicy`/`FadingRule` поле вроде `phase_source:
Literal["course_week", "assignment_number", "manual"]`, чтобы явно
перечислить допустимые источники. Отклонено: список типов пришлось бы
расширять при каждом новом сценарии (семестр, когорта, лабораторная работа,
произвольный флаг преподавателя), а сама структура не мешала бы протащить
через "manual" ровно то же `len(history)`. Вместо этого `turn_index: int`
остаётся единственным, нетипизированным параметром — весь контракт с
владельцем курса переносится в организационную практику ("откуда ты взял
это число"), а не в проверяемую фреймворком структуру. Пример из практики
(week course):

```python
# Автор бота вычисляет turn_index сам, из данных, которые сам же
# структурно хранит (не из диалога) — например, номер недели курса,
# заданный в расписании программы:
course_week = get_current_course_week(student_id)  # 1..14, внешний источник
response = await text_analysis(
    llm=llm, store=store, query=query,
    engine=engine, turn_index=course_week,
)
```

Единственная защита, которую фреймворк предоставляет — это явный
комментарий-инвариант в `base.py`/assumption-документе (этот раздел) и,
опционально, ревью на этапе кода. Runtime-проверку "не вычислен ли
turn_index из истории" фреймворк осознанно не делает — отличить `len(chat)`
от `course_week` программно нельзя, оба выглядят как обычный `int`.

**Паттерн: `turn_index` из оценки уверенности студента — разрешён, но
только через отдельный служебный вызов, не тот же, что отвечает
студенту.** Это не гипотетический сценарий — substrate прямо описывает
аналог: «скрытая семантическая диагностика рассуждения» из FD42-031
(«ФинМыш», см. B1 разбор живого ТЗ) — отдельный LLM-вызов, определяющий,
какого типа поддержка нужна дальше, специально помечен как «не итоговая
оценка компетентности, а диагностика состояния задачи», его метаданные
«сохраняются для аудита» и **не видны студенту**. Тот же принцип
переносится на `turn_index`:

```python
async def evaluate_confidence(llm: LLMProvider, student_answer: str) -> int:
    """Отдельный, служебный LLM-вызов — НЕ тот, что генерирует ответ
    студенту. Возвращает 1 (неуверенно) .. 3 (уверенно). Логировать
    отдельно (см. TODO про SupportEvent/A2, раздел 7) — это данные для
    аудита политики, не просто промежуточная переменная."""
    ...

confidence = await evaluate_confidence(llm, student_message)  # отдельный вызов
response = await text_analysis(
    llm=llm, store=store, query=query,
    engine=engine, turn_index=confidence,  # тот же параметр, другой источник
)
```

Что делает этот паттерн допустимым, а не «model-invented fading»:
(1) диагностика — отдельный вызов от финального ответа, значит её можно
аудировать/залогировать независимо; (2) она возвращает число/метку, а не
текст, который сразу идёт студенту; (3) критерий оценки ("что значит
уверенно") задаёт автор бота заранее в промпте диагностики — не
изобретается моделью на лету посреди диалога, отвечающего студенту.
Что остаётся domain-специфичным и осознанно не специфицировано
фреймворком: сам критерий "уверенности" (текст промпта `evaluate_confidence`,
шкала 1-3 vs 1-5, что считать признаком уверенности) — это решение автора
конкретного бота, как и содержание любого другого промпта навыка.

**Где `FadingRule` определяется физически — зафиксировано, изменений не
вносим.** Тот же принцип места, что уже решён в разделе 4 для
`SupportPolicy` целиком (живёт рядом с определением skill, не в отдельном
versioned store) — `FadingRule` не выделяется в отдельную сущность, это
просто одно из полей `SupportPolicy`, значит подчиняется тому же месту.
В `gb_edu_bot/app/skills.py` сейчас у всех пяти `SupportPolicy` стоит
`fading_rule=None` (в исходном коде бота такого дозирования никогда не
было) — если и когда автор конкретного навыка решит его включить, это
одна правка на месте, где сейчас `fading_rule=None`, например:

```python
TEXT_ANALYSIS_POLICY = SupportPolicy(
    ...,
    fading_rule=FadingRule(
        levels=["full_support", "reduced", "no_support"],
        turns_per_level=1,
        level_descriptions={...},
    ),
)
```

Никакого нового модуля/реестра под это не заводим — `levels`,
`turns_per_level` и `level_descriptions` целиком в руках автора конкретного
skill, framework их не хранит и не валидирует централизованно.

---

## 6. `fallback_reason` + `domain_notes` — почему один `fallback` было мало

Живой код (`dialogic_supportive_bot`, `gb_edu_bot`) содержал не одну, а
**несколько** причин отказа: `IRRELEVANT` (вопрос не по домену бота),
`PLAGIARISM` (просьба написать готовую работу за студента), плюс
оскорбления и запросы личных данных — каждая со своей формулировкой в
списке "признаков" внутри старого `intent_recognition()`. Изначальный
`Intent.fallback: bool` сливал все эти случаи в один флаг — вызывающему
коду было достаточно знать "не вызывать навык", но недостаточно, чтобы
показать разный ответ на "это не по теме" и на "я не пишу эссе за вас".

**Решение: `Intent.fallback_reason: str | None`** — свободная snake_case-
метка, заполняется только когда `fallback=True`. Два источника меток:

1. **Служебные, от самого движка** (не зависят от домена бота):
   `unparseable_response` (ответ модели не распарсился),
   `unknown_skill_name` (модель вернула имя навыка вне зарегистрированного
   списка), `no_skills_registered` (`route()` вызван до `register_skill()`).
2. **Domain-специфичные, от автора бота** — через новый параметр
   `domain_notes: str | None` в `DefaultIntentRouter.__init__`: свободный
   текст, перечисляющий признаки отказа и требуемые метки (аналог старого
   текстового блока "IRRELEVANT"/"PLAGIARISM", но структурированный —
   каждая причина явно связана с меткой, которую модель обязана вернуть).

```python
DOMAIN_NOTES = """
Reject (fallback) instead of picking a skill in these cases:
- fallback_reason="off_topic": вопрос не по педагогике.
- fallback_reason="academic_dishonesty": просьба написать эссе/домашку за студента.
- fallback_reason="abuse": оскорбление, провокация.
""".strip()

router = DefaultIntentRouter(llm, domain_notes=DOMAIN_NOTES)
```

**Почему `domain_notes` не типизирован (`Literal[...]`), тот же принцип,
что для `turn_index`/`phase_source`** (раздел 5, инвариант из Ledger
v1.4) — набор причин отказа у каждого бота свой (курс философии
отклоняет иначе, чем курс программирования), фиксированный список типов
пришлось бы расширять для каждого нового домена. `domain_notes` — просто
текст, встраиваемый в промпт классификации, framework не парсит и не
валидирует его содержимое, только передаёт дальше.

**Проверено на реальном LLM (302.ai, `gb_edu_bot`)**: 5 разных причин
отказа (off_topic, academic_dishonesty, unrelated_task_request, abuse,
personal_info_request) классифицированы верно на первый попытке, каждая
получила отдельное сообщение бота вместо одного универсального —
`bot_vk.py`, `FALLBACK_MESSAGES` по `intent.fallback_reason`.

---

## 7. Что осталось не решено этим документом (для следующего прохода)

- `IntentRouter.classify()` реализован (`DefaultIntentRouter` +
  `build_intent_prompt`, включая `domain_notes`/`fallback_reason` из
  раздела 6), но сам факт, что это инженерная, а не контрактная работа —
  остаётся в силе: SC01 не извлечён формально как отдельная JSON-схема
  (`DS`/`SM` для SC01 в реестре отсутствуют и в терминальном корпусе тоже —
  только consumer-минимумы и текстовая граница ответственности), поэтому
  текст промпта и набор `fallback_reason`-меток — не контракт фреймворка, а
  решение каждого конкретного бота, проверяемое только эмпирически на
  живом LLM, не формальным тестом против SC01.
- `SupportEvent` (лог факта показанной поддержки, `shown_to_learner`,
  `occurred_at`) — из `DS06`, нужен для `SM06` state machine и для B2-подобной
  истории поддержки, но не включён в интерфейс выше: он логирующий слой поверх
  `render_support_block()`, а не блокирующий контракт, и должен
  специфицироваться вместе с A2 (Usage & Cost Tracker) или отдельно.
- `SM06` целиком (`BEFORE_ATTEMPT → ATTEMPT_RECORDED → SUPPORT_ALLOWED →
  REDUCED_SUPPORT → NO_SUPPORT_TRANSFER`, `POLICY_DENIED`) — состояния сессии,
  не входящие в `DialoguePolicyEngine.Protocol` выше; вероятно, отдельный
  `SupportSession` объект, хранящий текущее состояние по `(actor_id, skill)`.
