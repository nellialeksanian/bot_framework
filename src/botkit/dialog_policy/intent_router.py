# B1. Dialogue Policy Engine — конкретная реализация IntentRouter (SC01).
#
# SC01 не извлечён формально в реестре контрактов (нет DS/SM-схемы) —
# сигнатура и промпт здесь выведены из живого кода, не из SC-каталога, см.
# "docs/B1 — Dialogue Policy Engine — assumption doc.md", раздел 1 и 6.
#
# Референс: dialogic_supportive_bot/app/skills.py, intent_recognition().
# Тот код жёстко зашивает 4 категории текстом прямо в промпт — специфично
# для одного бота. Здесь то же самое устроено параметрически: available_skills
# передаётся вызывающим кодом (DialoguePolicyEngine.route(), из
# register_skill()), а не переписывается в промпте для каждого нового бота.
#
# "anchor" (SkillAnchor) — мягкая привязка к последнему активному навыку,
# паттерн cicero_bot (last_skill_anchor: dict[chat_id -> {skill, question}]):
# помогает отличить продолжение текущего навыка от переключения на другой,
# не будучи жёстким состоянием диалога.

from __future__ import annotations

import json
import re

from botkit.dialog_policy.base import Intent, IntentRouter, SkillAnchor, SkillDescriptor
from botkit.llm.base import LLMProvider

FALLBACK_SKILL = "__fallback__"
"""Возвращается в Intent.skill, когда классификатор не смог уверенно выбрать
ни один из available_skills — вызывающий код (DialoguePolicyEngine.route())
не падает молча на несуществующем skill, а получает явный сигнал
(Intent.fallback=True) и сам решает, что делать: дефолтный навык, уточняющий
вопрос студенту, или отказ."""


def _render_skills_block(available_skills: list[SkillDescriptor]) -> str:
    return "\n".join(f'- "{s.name}": {s.description}' for s in available_skills)


def _render_anchor_block(anchor: SkillAnchor | None) -> str:
    if anchor is None:
        return "No active skill from a previous turn."
    return (
        f'Last active skill: "{anchor.skill}". '
        f"Last question asked to the student: {anchor.last_question!r}. "
        "If the student's message reads as a direct continuation of that "
        "question, prefer staying on this skill over switching."
    )


def _render_domain_notes_block(domain_notes: str | None) -> str:
    if not domain_notes:
        return (
            "No domain restrictions were provided. Judge fit against the "
            "AVAILABLE SKILLS descriptions above only."
        )
    return domain_notes


def build_intent_prompt(
    query: str,
    history: str,
    anchor: SkillAnchor | None,
    available_skills: list[SkillDescriptor],
    domain_notes: str | None = None,
) -> str:
    return f"""
Your task is to determine which ONE skill is the most appropriate response
to the student's message, given the skills available below.

===============================
AVAILABLE SKILLS
===============================
{_render_skills_block(available_skills)}

===============================
WHEN TO REJECT (fallback) INSTEAD OF PICKING A SKILL
===============================
{_render_domain_notes_block(domain_notes)}

If the message does not fit any available skill for a reason not covered
above, still return the fallback skill — do not force a bad fit.

===============================
ANCHOR (previous turn)
===============================
{_render_anchor_block(anchor)}

===============================
CONTEXT
===============================
Conversation history:
{history}

Student's message:
{query}

Return the result STRICTLY in the following JSON format WITHOUT markdown:
{{
    "skill": "<one of the skill names above, exactly as written>",
    "confidence": <float between 0 and 1, or null if unsure>,
    "fallback_reason": <short snake_case label if skill is "{FALLBACK_SKILL}", else null>
}}

If none of the available skills is a reasonable fit, return:
{{"skill": "{FALLBACK_SKILL}", "confidence": null, "fallback_reason": "<short snake_case label, e.g. off_topic, academic_dishonesty, abuse>"}}
""".strip()


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse_intent(raw: str, available_names: set[str]) -> Intent:
    match = _JSON_OBJECT_RE.search(raw)
    if match is None:
        return Intent(
            skill=FALLBACK_SKILL, confidence=None, fallback=True,
            fallback_reason="unparseable_response",
        )

    try:
        data = json.loads(match.group(0))
        skill = data["skill"]
        confidence = data.get("confidence")
        fallback_reason = data.get("fallback_reason")
    except (json.JSONDecodeError, KeyError, TypeError):
        return Intent(
            skill=FALLBACK_SKILL, confidence=None, fallback=True,
            fallback_reason="unparseable_response",
        )

    if skill not in available_names:
        # Модель либо явно вернула FALLBACK_SKILL с причиной, либо выдумала
        # имя навыка, которого нет в available_skills — во втором случае
        # fallback_reason из ответа модели не имеет смысла (она отвечала про
        # несуществующий skill), заменяем на явную метку.
        reason = fallback_reason if skill == FALLBACK_SKILL else "unknown_skill_name"
        return Intent(skill=FALLBACK_SKILL, confidence=None, fallback=True, fallback_reason=reason)

    return Intent(skill=skill, confidence=confidence, fallback=False)


class DefaultIntentRouter(IntentRouter):
    def __init__(self, llm: LLMProvider, domain_notes: str | None = None) -> None:
        self._llm = llm
        self._domain_notes = domain_notes
        # domain_notes — свободный текст автора бота, явно перечисляющий,
        # когда классификатор должен отклонить запрос вместо выбора навыка
        # (не по теме бота, просьба решить задачу за студента, оскорбления,
        # запрос личных данных — аналог старого блока "IRRELEVANT"/
        # "PLAGIARISM" в dialogic_supportive_bot/skills.py). Framework не
        # диктует содержание — это domain-специфичный текст, как и
        # SkillDescriptor.description для отдельного навыка.

    async def classify(
        self,
        query: str,
        history: str,
        anchor: SkillAnchor | None,
        available_skills: list[SkillDescriptor],
    ) -> Intent:
        if not available_skills:
            return Intent(
                skill=FALLBACK_SKILL, confidence=None, fallback=True,
                fallback_reason="no_skills_registered",
            )

        prompt = build_intent_prompt(query, history, anchor, available_skills, self._domain_notes)
        response = await self._llm.ainvoke(prompt)
        available_names = {s.name for s in available_skills}
        return _parse_intent(response.response, available_names)
