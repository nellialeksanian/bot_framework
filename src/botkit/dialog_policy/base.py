# B1. Dialogue Policy Engine — SC01 + SC06
# Источник: "Модули фреймворка — приоритет.md", раздел B1.
# (а) классификация интента, (б) support policy — что навыку разрешено говорить.

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class FadingRule:
    ...


class SkillAnchor:
    ...


@dataclass
class SupportPolicy:
    policy_id: str
    version: int
    target_action: str
    protected_difficulty: str
    attempt_gate: bool
    allowed_action_classes: list[str]
    deny_patterns: list[str]
    fading_rule: FadingRule | None


@dataclass
class Intent:
    skill: str
    confidence: float | None
    fallback: bool


class IntentRouter(Protocol):
    async def classify(self, query: str, history: str, anchor: SkillAnchor | None) -> Intent:
        ...


class PolicyStore(Protocol):
    async def get(self, policy_id: str) -> SupportPolicy:
        ...

    def build_prompt_fragment(self, policy: SupportPolicy) -> str:
        ...
