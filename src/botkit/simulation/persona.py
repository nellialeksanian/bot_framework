# C1. Simulation & Scenario Engine — SC02 — LLM-реализация PersonaSimulator
# поверх SQLiteSimulationLog + A1 LLMProvider.
#
# base.py описывает контракт (start/respond/complete), sqlite_store.py даёт
# persistence без LLM — этот модуль закрывает разрыв между ними: реальный
# вызов модели в роли персоны, по тому же паттерну, что LLMRevisionClassifier
# + ActiveSessionResolver закрывают его для B2 (attempts/sessions.py) —
# отдельный, более дешёвый/быстрый LLM-вызов-классификатор ПОСЛЕ основного
# ответа, а не встроенная в промпт персоны просьба "не давай советов" (той
# просьбе неоткуда взять structural evidence, если модель её всё равно
# нарушит — здесь нарушение остаётся видимым, а не тихо проходит).
#
# Что вынесено сюда (доменно-нейтрально, одинаково для любого бота с SC02):
#   - оборачивание LLMProvider.ainvoke() + запись в SimulationRun/ExecutionObstacle;
#   - role-boundary классификация как отдельный LLM-вызов, с fail-closed
#     поведением на неразборчивый ответ (как is_revision() в sessions.py).
#
# Что НЕ вынесено (остаётся у вызывающего бота/навыка):
#   - текст персоны/роли (persona_prompt) — предметная специфика конкретного
#     бота ("буквальный оператор" vs "клиент на переговорах"), не часть C1;
#   - что показать студенту, если respond() зафиксировал role-boundary
#     violation — деловое решение навыка (переспросить? скрыть совет? и т.д.).

from __future__ import annotations

import re
from typing import Protocol

from botkit.llm.base import LLMProvider
from botkit.simulation.base import SimulationRun
from botkit.simulation.sqlite_store import SQLiteSimulationLog


async def _invoke_text(llm: LLMProvider, prompt: str) -> str:
    """LLMProvider.ainvoke() по контракту A1 возвращает LLMResponse, но
    некоторые живые боты пока оборачивают легаси-клиент, чей .ainvoke()
    отдаёт str напрямую (см. тот же приём в LLMRevisionClassifier,
    attempts/sessions.py) — принимаем оба варианта."""
    response = await llm.ainvoke(prompt)
    if isinstance(response, str):
        return response
    return getattr(response, "response", None) or getattr(response, "raw", "") or ""


class RoleBoundaryClassifier(Protocol):
    """Решает, нарушил ли ответ симулятора границу роли (дал совет/переписал
    вместо реакции персоны). None = не уверен — вызывающий код должен
    трактовать это как потенциальное нарушение (fail-closed), не как чистый
    ответ, — в отличие от RevisionClassifier в B2, где неуверенность ведёт к
    вопросу студенту, здесь неуверенность не может блокировать диалог, поэтому
    трактуется консервативно в сторону "пометить как обнаруженное препятствие",
    а не в сторону "пропустить как есть"."""

    async def is_role_boundary_violation(self, persona_prompt: str, candidate_response: str) -> bool | None:
        ...


class LLMRoleBoundaryClassifier:
    """RoleBoundaryClassifier поверх LLMProvider (A1) — второй, отдельный
    LLM-вызов после генерации ответа персоны, не встроенный в тот же промпт.
    Требование "не давай советов" внутри промпта персоны ничего не проверяет
    постфактум — эта проверка происходит уже после того, как ответ получен."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def is_role_boundary_violation(self, persona_prompt: str, candidate_response: str) -> bool | None:
        prompt = f"""
Ты проверяешь, не нарушил ли ответ симулятора границу своей роли.

Роль, которую должен был играть симулятор:
{persona_prompt}

Ответ, который дал симулятор:
{candidate_response}

Является ли этот ответ СОВЕТОМ, ПОДСКАЗКОЙ или ПЕРЕПИСЫВАНИЕМ вместо
реакции персоны (например: "вам стоит...", "лучше написать так...",
готовый исправленный текст) — то есть выходом за пределы роли — или это
обычная реакция персоны в рамках заданной роли?

Ответь СТРОГО в формате JSON, без markdown:
{{"violation": true}} — если это нарушение роли (совет/переписывание)
{{"violation": false}} — если это нормальная реакция персоны
{{"violation": null}} — если невозможно уверенно определить
""".strip()

        raw = await _invoke_text(self._llm, prompt)
        match = re.search(r'"violation"\s*:\s*(true|false|null)', raw, re.IGNORECASE)
        if match is None:
            return None
        value = match.group(1).lower()
        if value == "true":
            return True
        if value == "false":
            return False
        return None


class LLMPersonaSimulator:
    """PersonaSimulator (SC02) — LLMProvider в заданной роли поверх
    SQLiteSimulationLog. Один экземпляр — одна роль/policy (persona_prompt),
    как один навык использует одну SupportPolicy (B1) — разные роли требуют
    разных экземпляров, не параметра на каждый respond().

    role_classifier опционален: без него respond() не проверяет
    role-boundary violation вовсе (ровно так же, как ActiveSessionResolver
    без classifier не делает автоматической классификации) — обстекло для
    ботов, которым не нужна эта проверка, а не скрытая деградация.
    """

    def __init__(
        self,
        llm: LLMProvider,
        log: SQLiteSimulationLog,
        persona_prompt: str,
        *,
        role_classifier: RoleBoundaryClassifier | None = None,
    ) -> None:
        self._llm = llm
        self._log = log
        self._persona_prompt = persona_prompt
        self._role_classifier = role_classifier

    async def start(
        self,
        actor_id: str,
        task_ref: str,
        simulator_policy_version: int,
        literalness_profile_version: int,
        *,
        attempt_ref: str | None = None,
        require_attempt: bool = False,
        persona_profile_version: int | None = None,
    ) -> SimulationRun:
        return await self._log.start(
            actor_id,
            task_ref,
            simulator_policy_version,
            literalness_profile_version,
            attempt_ref=attempt_ref,
            require_attempt=require_attempt,
            persona_profile_version=persona_profile_version,
        )

    async def respond(self, run_id: str, student_message: str) -> str:
        prompt = f"{self._persona_prompt}\n\nСообщение студента:\n{student_message}"
        candidate_response = await _invoke_text(self._llm, prompt)

        # R1001 FD42-020: каждый ход пишется автоматически, до классификации
        # роли — иначе история диалога теряется для навыка, который никогда
        # сам не вызывает record_turn() (как BotResponse в B2 пишется внутри
        # record_response(), а не отдельным действием навыка).
        await self._log.record_turn(run_id, student_message, candidate_response)

        if self._role_classifier is not None:
            violation = await self._role_classifier.is_role_boundary_violation(
                self._persona_prompt, candidate_response
            )
            # None (не уверен) трактуется как потенциальное нарушение —
            # fail-closed: препятствие лучше ложно отметить, чем пропустить
            # молча (в отличие от B2, здесь нет "переспросить студента",
            # только "зафиксировать или нет").
            if violation is not False:
                await self._log.record_obstacle(
                    run_ref=run_id,
                    step_ref="respond",
                    obstacle_type="OTHER",
                    evidence_ref=candidate_response,
                    advisory_content_present=True,
                )

        return candidate_response

    async def complete(self, run_id: str) -> SimulationRun:
        return await self._log.complete(run_id)

    async def get_active_run(self, actor_id: str, task_ref: str) -> SimulationRun | None:
        return await self._log.get_active_run(actor_id, task_ref)

    async def get_turns(self, run_id: str):
        return await self._log.get_turns(run_id)
