# B1. Dialogue Policy Engine — конкретная реализация DialoguePolicyEngine.
#
# register_skill() держит SupportPolicy в памяти, keyed по skill name — это
# основной путь (docs/"B1 — Dialogue Policy Engine — assumption doc.md",
# раздел 4): policy живёт рядом с определением skill, не в отдельном
# versioned store. load_policy_overrides() опционально накатывает YAML из
# policies/ поверх уже зарегистрированных policy — для A/B-теста
# формулировок без передеплоя кода навыка (раздел 4, "policies/... не хранит
# SupportPolicy как основной путь").
#
# check_attempt_gate() (CP11A) и render_support_block() (CP11B) сознательно
# разделены, потому что substrate v1.3 (P009) показывает, что это независимо
# применимые примитивы: attempt_gate=False — легитимное значение (N/A), а не
# недоработка, для навыков без предшествующей человеческой попытки.

from __future__ import annotations

from pathlib import Path

from botkit.attempts.base import AttemptStore
from botkit.authority.base import AuthorityGate, Role
from botkit.dialog_policy.base import (
    DialoguePolicyEngine,
    Intent,
    IntentRouter,
    SkillAnchor,
    SkillDescriptor,
    SkillHandler,
    SupportPolicy,
)


class UnknownSkillError(Exception):
    """register_skill() не был вызван для этого имени навыка до обращения
    к check_attempt_gate()/render_support_block() — ошибка конфигурации
    вызывающего кода, а не runtime-состояние, которое стоит проглатывать."""


class DefaultDialoguePolicyEngine(DialoguePolicyEngine):
    def __init__(self, intent_router: IntentRouter) -> None:
        self._intent_router = intent_router
        self._policies: dict[str, SupportPolicy] = {}
        self._handlers: dict[str, SkillHandler] = {}
        self._descriptions: dict[str, str] = {}
        self._required_roles: dict[str, Role | None] = {}

    def register_skill(
        self,
        name: str,
        handler: SkillHandler,
        support_policy: SupportPolicy,
        description: str,
        required_role: Role | None = None,
    ) -> None:
        self._handlers[name] = handler
        self._policies[name] = support_policy
        self._descriptions[name] = description
        self._required_roles[name] = required_role

    def load_policy_overrides(self, policies_dir: str | Path) -> None:
        """Накатывает YAML-файлы из policies_dir поверх уже зарегистрированных
        policy — файл с именем "<skill>.yaml" заменяет policy для "<skill>",
        только если этот skill уже был зарегистрирован через register_skill().
        Тихо пропускает файлы без соответствующего зарегистрированного skill,
        потому что policies/ может содержать оверрайды для ещё не
        загруженных на этом инстансе навыков (напр. общий репозиторий policy
        для нескольких ботов)."""
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "load_policy_overrides() requires PyYAML — "
                "install it or construct SupportPolicy directly in Python code"
            ) from exc

        for path in Path(policies_dir).glob("*.yaml"):
            skill = path.stem
            if skill not in self._policies:
                continue
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            self._policies[skill] = _policy_from_dict(data)

    def _require_policy(self, skill: str) -> SupportPolicy:
        try:
            return self._policies[skill]
        except KeyError:
            raise UnknownSkillError(
                f"no SupportPolicy registered for skill {skill!r} — "
                "call register_skill() before check_attempt_gate()/render_support_block()"
            ) from None

    async def check_attempt_gate(
        self, skill: str, actor_id: str, task_ref: str, attempts: AttemptStore
    ) -> bool:
        policy = self._require_policy(skill)
        if not policy.attempt_gate:
            return True  # N/A для этой семьи навыков — не ошибка, см. CP11A
        return await attempts.latest(actor_id, task_ref) is not None

    async def check_authority_gate(
        self, skill: str, platform_user_id: str, platform: str, authority: AuthorityGate
    ) -> bool:
        required_role = self._require_required_role(skill)
        if required_role is None:
            return True  # N/A для этого навыка — открыт любой роли, см. CP11A-аналогия
        return await authority.require_role(platform_user_id, platform, required_role)

    def _require_required_role(self, skill: str) -> Role | None:
        if skill not in self._required_roles:
            raise UnknownSkillError(
                f"no SupportPolicy registered for skill {skill!r} — "
                "call register_skill() before check_authority_gate()"
            )
        return self._required_roles[skill]

    def render_support_block(self, skill: str, turn_index: int) -> str:
        policy = self._require_policy(skill)
        lines = [f"## Support policy: {policy.target_action}"]

        if policy.deny_patterns:
            lines.append("### What you must not do")
            lines.extend(f"- {pattern}" for pattern in policy.deny_patterns)

        if policy.allowed_action_classes:
            allowed = ", ".join(policy.allowed_action_classes)
            lines.append(f"### Allowed action classes: {allowed}")

        if policy.fading_rule is not None:
            level_index = min(
                turn_index // policy.fading_rule.turns_per_level,
                len(policy.fading_rule.levels) - 1,
            )
            current_level = policy.fading_rule.levels[level_index]
            lines.append(f"### Current support level: {current_level}")
            description = (policy.fading_rule.level_descriptions or {}).get(current_level)
            if description:
                lines.append(description)

        return "\n".join(lines)

    async def route(
        self, query: str, history: str, anchor: SkillAnchor | None
    ) -> Intent:
        available_skills = [
            SkillDescriptor(name=name, description=description)
            for name, description in self._descriptions.items()
        ]
        return await self._intent_router.classify(query, history, anchor, available_skills)


def _policy_from_dict(data: dict) -> SupportPolicy:
    from botkit.dialog_policy.base import FadingRule

    fading_data = data.get("fading_rule")
    fading_rule = (
        FadingRule(levels=fading_data["levels"], turns_per_level=fading_data["turns_per_level"])
        if fading_data
        else None
    )
    return SupportPolicy(
        policy_id=data["policy_id"],
        version=data["version"],
        target_action=data["target_action"],
        protected_difficulty=data["protected_difficulty"],
        attempt_gate=data["attempt_gate"],
        allowed_action_classes=list(data["allowed_action_classes"]),
        deny_patterns=list(data["deny_patterns"]),
        fading_rule=fading_rule,
    )
