# Human Authority Gate (B4): как пользоваться

Практическое руководство по `botkit.authority` — коду в `src/botkit/authority/`.
Про архитектурные решения и инварианты см. раздел B4 в
[«Модули фреймворка — приоритет.md»](Модули%20фреймворка%20—%20приоритет.md);
здесь — как это реально вызывать. Живой пример подключения к реальному
навыку — `teacher_verification` в `gb_edu_bot/app/skills.py` и
`gb_edu_bot/app/bot_vk.py`.

---

## Общая схема

```
EnvIdentityGate.from_env()          (botkit.authority.identity_env)
   │  — читает TEACHER_IDS_<PLATFORM> из env один раз при старте бота
   ▼
DefaultAuthorityGate(identity_gate)   (botkit.authority.gate)
   │
   ├── require_role(platform_user_id, platform, required) → bool
   │      структурная проверка "у этого ID есть роль X?" — используется
   │      напрямую ИЛИ через check_authority_gate() ниже
   │
   └── record_decision(event: DecisionEvent) → None    (см. "Вторая часть" ниже —
          get_decisions(candidate_ref) → [DecisionEvent, ...]   реализовано,
                                                                  но пока нигде
                                                                  не вызывается)

DefaultDialoguePolicyEngine.register_skill(..., required_role=Role.TEACHER)
   │  — привязка "этому навыку нужна такая роль" (B1, не B4 —
   │    require_role сам по себе про роль ничего про skill не знает)
   ▼
engine.check_authority_gate(skill, platform_user_id, platform, authority)
   │  — зеркало check_attempt_gate() (B2): если required_role не задан
   │    при регистрации skill — всегда True, N/A
   ▼
True/False — вызывать handler навыка или нет
```

Две независимые части, обе из раздела B4, но с очень разным статусом
использования прямо сейчас:

- **SC16 (Identity/Access)** — `EnvIdentityGate` + `require_role` +
  `check_authority_gate` — **реально работает** в `gb_edu_bot`, закрывает
  `teacher_verification` от студентов.
- **SC14 (Human Decision Service)** — `DecisionEvent` + `record_decision` —
  реализовано и покрыто тестами, но **не имеет ни одного вызывающего кода**
  ни в одном навыке. См. раздел «Вторая часть» ниже — почему, и когда это
  должно измениться.

---

## Шаг 1 — собрать IdentityGate из env

```python
from botkit.authority.identity_env import EnvIdentityGate

identity_gate = EnvIdentityGate.from_env()
```

Один раз при старте бота, как `SQLiteAttemptStore` в B2. Читает `os.environ`
целиком, ищет все переменные вида `TEACHER_IDS_<PLATFORM>` и парсит каждую
через запятую в множество ID:

```bash
# .env
TEACHER_IDS_VK = "268075089,111222333"
TEACHER_IDS_TELEGRAM = "42"
```

Списки ведутся **раздельно по платформам** — один и тот же человек имеет
разные ID в VK и Telegram, общий список смешал бы их и мог случайно выдать
роль TEACHER чужому пользователю на другой платформе, которому просто
повезло получить тот же числовой ID.

Пусто или переменная не задана вовсе → на этой платформе никто не получает
роль TEACHER, без ошибки при старте — это осознанный fail-closed дефолт, не
баг конфигурации.

Для тестов — тот же метод принимает явный словарь вместо `os.environ`:

```python
identity_gate = EnvIdentityGate.from_env({"TEACHER_IDS_VK": "123"})
```

---

## Шаг 2 — собрать AuthorityGate

```python
from botkit.authority.gate import DefaultAuthorityGate

authority = DefaultAuthorityGate(identity_gate)
```

`DefaultAuthorityGate` не хранит свой отдельный список ID — он полностью
делегирует резолв роли в переданный `IdentityGate`. Единственный источник
структурной идентичности в боте один, чтобы два списка допущенных ID не
разъехались друг с другом со временем.

---

## Шаг 3 — привязать роль к навыку при регистрации

```python
engine.register_skill(
    "teacher_verification",
    handler=teacher_verification,
    support_policy=TEACHER_VERIFICATION_POLICY,
    description="...",
    required_role=Role.TEACHER,   # новый параметр, по умолчанию None
)
```

`required_role=None` (дефолт) — навык открыт любой роли, `check_authority_gate()`
для него всегда вернёт `True` без обращения к `IdentityGate` вообще. Задавать
`required_role` нужно только для навыков, которые реально должны быть
недоступны части пользователей

`description` при этом не меняется и продолжает решать другую задачу —
она сигнал для `IntentRouter`, ЧТО за запрос ("похоже на проверку
преподавателя"), а `required_role` — отдельная структурная проверка,
КТО его прислал. Обе проверки нужны вместе: без `description` навык не
попадёт в классификацию вообще, без `required_role` он попадёт к любому,
кто напишет текст, похожий на запрос преподавателя.

---

## Шаг 4 — проверить перед вызовом навыка

```python
if not await state.engine.check_authority_gate(
    intent.skill, str(user_id), "vk", authority=state.authority
):
    await message.answer("❌ Эта функция доступна только преподавателю.")
    return
```

Вызывается **после** `route()` (получен `intent.skill`) и **до** любого
другого шага, зависящего от того, что навык вообще выполнится — в
`gb_edu_bot` это первая проверка сразу после fallback-блока, раньше B2
session-resolve. Место выбрано так же, как `check_attempt_gate()` в B2:
gate должен сработать раньше, чем начнётся любая побочная работа (запись
в AttemptStore, трата токенов на LLM), а не после.

Один и тот же вызов безопасен для всех навыков сразу, включая те, что не
требуют роли вовсе — для них `check_authority_gate()` вернёт `True` на
первой же строке (`required_role is None`), не трогая `authority`/
`IdentityGate`.

---

## Вторая часть — DecisionEvent (SC14), реализовано, но не подключено

```python
from botkit.authority.base import DecisionEvent, Role
from datetime import datetime, timezone

event = DecisionEvent(
    decision_id="d1",
    candidate_ref=attempt.attempt_id,     # ссылка на B2 Attempt/BotResponse,
                                            # не копия его содержимого
    decision_class="PEDAGOGICAL_JUDGMENT",
    actor_role=Role.TEACHER,
    decision="ACCEPT",                     # или "EDIT" / "REJECT" / "ESCALATE"
    evidence_refs=[],
    occurred_at=datetime.now(timezone.utc),
)
await authority.record_decision(event)

recorded = await authority.get_decisions(attempt.attempt_id)
```

`record_decision()` — append-only, как `Attempt` в B2 и `RaterRecord` в B3:
решение никогда не перезаписывается и не усредняется, только добавляется.
`get_decisions(candidate_ref)` без единой записи возвращает пустой список —
это и есть инвариант **отсутствие `DecisionEvent` != ACCEPT**: молчание,
таймаут или отсутствие вызова не читаются нигде в коде как согласие.

**Почему в `gb_edu_bot` этого вызова пока нет ни в одном навыке.**
`record_decision()` имеет смысл только в момент, когда *реальный человек*
принял *реальное* решение над машинным кандидатом. В текущих навыках такого
момента просто не происходит:

- `feedback` генерирует текст и сразу отдаёт его студенту — преподаватель
  вообще не участвует в цикле, значит принимать `DecisionEvent` не от кого.
  Кандидат (текст фидбека + `labels_found`/`evidence`) при этом уже полностью
  залогирован через `BotResponse` (B2) и `RaterRecord` (B3, `rater_qualification="llm"`)
  — этого достаточно для SHADOW-режима (реестр контрактов, SC10):
  накапливать историю для будущего аудита, не выдумывая фиктивное решение,
  которого не было.
- `teacher_verification` — справочная функция: она отвечает на вопрос
  преподавателя, но не производит кандидата, который кто-то должен принять
  или отклонить.

**Когда это должно появиться.** В момент реального перехода SHADOW→ASSISTED
(см. реестр контрактов, раздел SC10 и разбор Овсянниковой FD42-028): когда
преподаватель начнёт явно смотреть на уже накопленные `RaterRecord`/
`BotResponse` из `feedback` и подтверждать/отклонять их перед тем, как
финальный текст уйдёт студенту. `candidate_ref` в этом будущем
`DecisionEvent` будет ссылаться на уже существующий `attempt.attempt_id` —
никакой новой структуры для самого кандидата заводить не придётся, только
сам акт решения.

До этого момента `record_decision()`/`get_decisions()` — рабочий, покрытый
тестами (`tests/test_authority_gate.py`) примитив без потребителя, а не
недоделанная часть модуля.

---

## Инварианты

- `IdentityGate.resolve_role()` (и, соответственно, `require_role()`)
  никогда не вызывает LLM — чисто структурная проверка списка ID.
- Неизвестный `platform_user_id` по умолчанию получает `Role.STUDENT`
  (fail-closed в сторону наименьших прав), не бросает исключение.
- Списки `TEACHER_IDS_<PLATFORM>` не смешиваются между платформами — тот же
  ID на другой платформе не наследует роль.
- `required_role=None` при `register_skill()` — легитимное значение (N/A),
  а не недоработка, для навыков без ограничения по роли, симметрично
  `attempt_gate=False` в B1/B2.
- `record_decision()` — append-only, решение никогда не перезаписывается.
- Отсутствие `DecisionEvent` для `candidate_ref` означает «решение ещё не
  принято», не ACCEPT — таймаут или молчание никогда не создают запись
  автоматически, ни в коде движка, ни в вызывающих навыках.
- `DefaultAuthorityGate` хранит решения **в памяти** (`list`, не SQLite) —
  как и `SQLiteAttemptStore` до появления персистентности в B2, это
  осознанно временное состояние: пока нет ни одного реального вызывающего
  кода, персистентность не требуется, но при первом реальном подключении
  (см. «Вторая часть» выше) её нужно будет добавить раньше, чем начнётся
  потеря данных при рестарте.
