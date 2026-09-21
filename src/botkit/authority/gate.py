# B4. Human Authority Gate — конкретная реализация AuthorityGate (SC14).
#
# Источник: "Модули фреймворка — приоритет.md", раздел B4 — require_role()
# проверяет структурную роль (через IdentityGate, B4/SC16), record_decision()
# фиксирует переход "машинный кандидат -> значимый статус" как явное событие.
#
# Ключевой инвариант из реестра (SC14, все 3 deep-разбора FD42-023/028/031):
# молчание/таймаут != согласие. Отсюда — record_decision() ничего не решает
# сам и не имеет "default ACCEPT" ветки: вызывающий код обязан явно
# сконструировать DecisionEvent с decision="ACCEPT"/"EDIT"/"REJECT"/"ESCALATE";
# отсутствие вызова record_decision() и есть отсутствие решения, а не accept.
#
# require_role() делегирует в IdentityGate (B4/SC16), а не хранит свой
# отдельный список ID — единственный источник структурной идентичности
# в боте один, чтобы не рассинхронизировать два списка допущенных ID.

from __future__ import annotations

from botkit.authority.base import DecisionEvent, IdentityGate, Role


class DefaultAuthorityGate:
    """AuthorityGate поверх IdentityGate + append-only лог решений."""

    def __init__(self, identity: IdentityGate):
        self._identity = identity
        self._decisions: list[DecisionEvent] = []

    async def require_role(
        self, platform_user_id: str, platform: str, required: Role
    ) -> bool:
        actual = await self._identity.resolve_role(platform_user_id, platform)
        return actual == required

    async def record_decision(self, event: DecisionEvent) -> None:
        # append-only, как Attempt (B2) и RaterRecord (B3) — решение никогда
        # не перезаписывается и не усредняется, только добавляется.
        self._decisions.append(event)

    async def get_decisions(self, candidate_ref: str) -> list[DecisionEvent]:
        return [e for e in self._decisions if e.candidate_ref == candidate_ref]
