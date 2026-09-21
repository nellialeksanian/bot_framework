# B1. Dialogue Policy Engine — SC01 + SC06
# Источник: "Модули фреймворка — приоритет.md", раздел B1, уточнён
# "B1 — Dialogue Policy Engine — assumption doc.md" (docs/).
# (а) IntentRouter — какой навык уместен сейчас (SC01, не извлечён формально
#     в реестре контрактов — сигнатура выведена из кода, не из SC-каталога).
# (б) SupportPolicy — что этому навыку разрешено говорить и с какой дозой
#     поддержки (SC06, DS06 в substrate v1.3).
#
# CP11 (substrate v1.3, P009) распался на два независимо применимых примитива,
# отражённых как два разных метода DialoguePolicyEngine, а не один:
#   CP11A protected first attempt -> check_attempt_gate() (структурный gate
#         через B2/AttemptStore, может быть N/A: attempt_gate=False)
#   CP11B support dose + reveal/fading -> render_support_block() (чистая
#         функция, текст для промпта, не вызывает LLM и не имеет side effects)

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Protocol

from botkit.attempts.base import AttemptStore
from botkit.authority.base import AuthorityGate, Role


@dataclass
class FadingRule:
    # как дозируется поддержка со временем/по фазе — CP11B
    levels: list[str]              # напр. ["full_support", "reduced", "no_support"]
    turns_per_level: int
    level_descriptions: dict[str, str] | None = None
    # что означает каждый уровень для модели — имя уровня само по себе
    # ("no_support") не самодостаточная инструкция, модель должна получить
    # текст вроде "не давай примеров, только один вопрос без пояснений"


@dataclass
class SkillAnchor:
    # "мягкая привязка" к последнему активному навыку — паттерн cicero_bot
    # (last_skill_anchor: dict[chat_id -> {skill, question}])
    skill: str
    last_question: str


@dataclass
class SupportPolicy:
    # Данные педагогического владельца (домена/автора навыка), не движка:
    # движок только исполняет жизненный цикл этой структуры.
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
    # Literal: конкретные причины (off_topic, academic_dishonesty, abuse,
    # personal_info_request, unparseable_response, unknown_skill_name) —
    # решение автора конкретного бота через domain_notes в build_intent_prompt,
    # см. docs/"Dialog Policy — как пользоваться.md". Framework не диктует
    # набор причин так же, как не диктует источник turn_index.


@dataclass
class SkillDescriptor:
    # Что IntentRouter видит о навыке при классификации — собирается движком
    # из уже зарегистрированных навыков, а не хранится внутри router'а
    # (router остаётся stateless, как AttemptStore в check_attempt_gate).
    name: str
    description: str                    # когда этот навык уместен — заменяет
                                         # блок текста, зашитый в промпт
                                         # классификатора вручную (напр.
                                         # intent_recognition() в живом коде)


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
        required_role: Role | None = None,
    ) -> None:
        """required_role — привязка skill к роли (B4/SC14+SC16). None (по
        умолчанию) значит "любая роль" — не всякий навык нуждается в
        авторизации (напр. position_clarification открыт студенту и
        преподавателю одинаково); только навыки вроде "выставить оценку"
        или "показать чужую работу" задают required_role явно."""
        ...

    async def check_attempt_gate(
        self, skill: str, actor_id: str, task_ref: str, attempts: AttemptStore
    ) -> bool:
        """CP11A. False -> вызов навыка блокируется до записи Attempt в B2.
        Если зарегистрированная для skill SupportPolicy.attempt_gate=False,
        всегда возвращает True (N/A — не всякая семья навыков имеет
        предшествующую человеческую попытку, напр. C1 deliberate-defect)."""
        ...

    async def check_authority_gate(
        self, skill: str, platform_user_id: str, platform: str, authority: AuthorityGate
    ) -> bool:
        """B4/SC14+SC16, зеркало check_attempt_gate(). False -> вызов навыка
        блокируется, роль пользователя не совпадает с required_role,
        зарегистрированной для skill. Если register_skill() был вызван с
        required_role=None, всегда возвращает True (N/A — навык открыт всем
        ролям), симметрично attempt_gate=False в check_attempt_gate()."""
        ...

    def render_support_block(self, skill: str, turn_index: int) -> str:
        """CP11B. Чистая функция без внешних вызовов: deny_patterns +
        allowed_action_classes (+ применённый fading_rule по turn_index) -> текст
        для вставки в промпт навыка. Не обращается к LLM и не имеет side effects.

        turn_index обязан приходить от вызывающего кода как внешний,
        заданный вне диалога вход (напр. номер недели курса, этап задания) —
        НЕ как производная от числа сообщений студента/ChatMemory. Ledger
        v1.4 прямо называет deny path "постепенное ослабление запрета после
        повторного давления": turn_index, растущий от активности студента в
        чате, позволяет выманить no_support -> full_support просто настаивая
        (см. assumption doc, раздел про Инвариант из Ledger v1.4)."""
        ...

    async def route(
        self, query: str, history: str, anchor: SkillAnchor | None
    ) -> Intent:
        """Собирает SkillDescriptor для всех навыков, зарегистрированных через
        register_skill() (single source of truth — не дублируется отдельно
        в IntentRouter), и передаёт их в IntentRouter.classify()."""
        ...
