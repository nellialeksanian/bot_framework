# B4. Human Authority Gate — SC14 + SC16
# Источник: "Модули фреймворка — приоритет.md", раздел B4.
# (а) типизированное решение человека поверх машинного кандидата (SC14),
# (б) структурная проверка роли пользователя (SC16).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Literal, Protocol


class Role(Enum):
    STUDENT = "student"
    TEACHER = "teacher"
    RESEARCHER = "researcher"


class IdentityGate(Protocol):
    async def resolve_role(self, platform_user_id: str, platform: str) -> Role:
        """Структурно: список допущенных ID / приглашение-токен / SSO.
        НЕ текстовый эвристический разбор, НЕ вызов LLM."""
        ...


@dataclass
class DecisionEvent:
    decision_id: str
    candidate_ref: str
    decision_class: Literal["PEDAGOGICAL_JUDGMENT", "REVIEW", "RELEASE", "GOVERNANCE"]
    actor_role: Role
    decision: Literal["ACCEPT", "EDIT", "REJECT", "ESCALATE"]
    evidence_refs: list[str]
    occurred_at: datetime
    # инвариант: отсутствие DecisionEvent != ACCEPT. Таймаут не создаёт запись.


class AuthorityGate(Protocol):
    async def require_role(self, platform_user_id: str, platform: str, required: Role) -> bool:
        """Делегирует резолв роли в IdentityGate (B4/SC16) — platform обязателен
        по той же причине, что и в IdentityGate.resolve_role(): один и тот же
        человек имеет разные platform_user_id на разных платформах."""
        ...

    async def record_decision(self, event: DecisionEvent) -> None:
        ...
