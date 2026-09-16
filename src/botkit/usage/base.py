# A2. Usage & Cost Tracker
# Источник: "Модули фреймворка — приоритет.md", раздел A2.
# Логирование каждого LLM-вызова: токены, стоимость, контекст навыка, привязка к пользователю.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol


@dataclass
class UsageEvent:
    timestamp: datetime
    user_id: str
    skill_context: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: Decimal
    input_text: str
    output_text: str
    meta: dict


class UsageStats:
    ...


class UsageTracker(Protocol):
    async def log(self, event: UsageEvent) -> None:
        ...

    async def stats(self, user_id: str | None, skill_context: str | None) -> UsageStats:
        ...
