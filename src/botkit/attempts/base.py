# B2. Attempt & Revision Store — SC07
# Источник: "Модули фреймворка — приоритет.md", раздел B2.
# Персистентная история попыток пользователя, неизменяемость исходной версии,
# связь parent -> child между ревизиями.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class Attempt:
    attempt_id: str
    actor_id: str
    task_ref: str
    content: str
    content_hash: str
    created_at: datetime
    origin: Literal["HUMAN"]
    parent_attempt_id: str | None


class AttemptStore(Protocol):
    async def record(
        self, actor_id: str, task_ref: str, content: str, parent_id: str | None
    ) -> Attempt:
        ...

    async def get_lineage(self, attempt_id: str) -> list[Attempt]:
        ...

    async def latest(self, actor_id: str, task_ref: str) -> Attempt | None:
        ...
