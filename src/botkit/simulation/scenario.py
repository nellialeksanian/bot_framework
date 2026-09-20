# C1. Simulation & Scenario Engine — SC03 — LLM-реализация ScenarioGenerator
# поверх SQLiteScenarioGenerator + A1 LLMProvider.
#
# Как и persona.py для SC02: base.py — контракт, sqlite_store.py —
# persistence без LLM, этот модуль — реальный вызов модели, которая придумывает
# defects. SQLiteScenarioGenerator уже проверяет DEFECT_COUNT_MISMATCH
# (T006-02) на входе — эта обёртка отвечает только за то, чтобы получить от
# LLM пригодный list[dict] и отдать его дальше, не подделывая count самой.

from __future__ import annotations

import json
import re

from botkit.llm.base import LLMProvider
from botkit.simulation.base import DefectSet
from botkit.simulation.sqlite_store import SQLiteScenarioGenerator


async def _invoke_text(llm: LLMProvider, prompt: str) -> str:
    """См. persona.py — тот же приём терпимости к легаси str-клиентам."""
    response = await llm.ainvoke(prompt)
    if isinstance(response, str):
        return response
    return getattr(response, "response", None) or getattr(response, "raw", "") or ""


class DefectGenerationParseError(Exception):
    """LLM не вернула разбираемый JSON-список дефектов.

    Явная ошибка вместо тихой генерации пустого/усечённого defects — иначе
    DefectCountMismatchError из SQLiteScenarioGenerator замаскировала бы
    настоящую причину (парсинг, не число дефектов).
    """


class LLMScenarioGenerator:
    """ScenarioGenerator (SC03) — LLMProvider, генерирующий контролируемый
    набор дефектов, поверх SQLiteScenarioGenerator. Один экземпляр — один
    домен/эталон (source_standard_prompt), как LLMPersonaSimulator — одна
    роль на экземпляр.
    """

    def __init__(
        self,
        llm: LLMProvider,
        store: SQLiteScenarioGenerator,
        source_standard_prompt: str,
        *,
        model_config_version: str,
        generator_policy_version: int = 1,
    ) -> None:
        self._llm = llm
        self._store = store
        self._source_standard_prompt = source_standard_prompt
        self._model_config_version = model_config_version
        self._generator_policy_version = generator_policy_version

    async def generate_defect_set(
        self,
        task_ref: str,
        source_standard_ref: str,
        intended_defect_count: int,
        model_config_version: str | None = None,
    ) -> DefectSet:
        # R0301 FD42-006: интервенция обязана предъявить студенту НОВЫЙ
        # текст с намеренно внедрёнными ошибками как объект анализа — не
        # рецензию/сравнение с эталоном. Промпт явно запрещает второе,
        # потому что модель, имея эталон перед глазами, по умолчанию
        # склоняется к "найти, чего не хватает в эталоне", а не "написать
        # текст и испортить его самой" (это разные задачи, легко путаются).
        prompt = f"""
{self._source_standard_prompt}

ЗАДАЧА: напиши НОВЫЙ связный учебный текст (несколько абзацев) на основе
эталонного жизненного цикла выше, описывающий его так, как если бы это был
раздел учебника. В этот текст ты должен НАМЕРЕННО ВНЕДРИТЬ РОВНО
{intended_defect_count} фактических ошибок — то есть текст должен содержать
{intended_defect_count} утверждений, которые прямо противоречат эталону
(например: другой хозяин, другая стадия, другой путь заражения).

ЭТО НЕ РЕЦЕНЗИЯ НА ЭТАЛОН И НЕ СРАВНЕНИЕ — ты пишешь свой собственный текст
с нуля и сам решаешь, в каких {intended_defect_count} местах исказить факты.
Студент увидит только твой текст (не эталон) и должен будет найти искажения.

Ответь СТРОГО в формате JSON, без markdown, одним объектом:
{{
  "stimulus_text": "<полный текст учебного материала с внедрёнными ошибками>",
  "defects": [
    {{"defect_type": "...", "locator": "цитата или место в stimulus_text, где искажение", "description": "в чём именно искажён факт и что было бы верно"}},
    ...
  ]
}}
"defects" должен содержать ровно {intended_defect_count} элементов, каждый
из которых указывает на реально присутствующее в stimulus_text искажение.
""".strip()

        raw = await _invoke_text(self._llm, prompt)
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match is None:
            raise DefectGenerationParseError(f"no JSON object found in LLM output: {raw!r}")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise DefectGenerationParseError(f"could not parse defect set JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise DefectGenerationParseError(f"expected a JSON object, got {type(parsed).__name__}")

        stimulus_text = parsed.get("stimulus_text")
        defects = parsed.get("defects")
        if not isinstance(stimulus_text, str) or not stimulus_text.strip():
            raise DefectGenerationParseError(f"missing or empty 'stimulus_text' in LLM output: {parsed!r}")
        if not isinstance(defects, list):
            raise DefectGenerationParseError(f"expected 'defects' to be a JSON list, got {type(defects).__name__}")

        # DEFECT_COUNT_MISMATCH (T006-02) проверяется внутри store — не
        # дублируем проверку здесь, просто передаём фактически полученное.
        return await self._store.generate_defect_set(
            task_ref,
            source_standard_ref,
            intended_defect_count,
            model_config_version or self._model_config_version,
            generator_policy_version=self._generator_policy_version,
            stimulus_text=stimulus_text,
            defects=defects,
        )

    async def record_diagnosis(
        self,
        defect_set_ref: str,
        actor_id: str,
        found_items: list[str],
        correction_prompt_ref: str | None = None,
    ):
        return await self._store.record_diagnosis(
            defect_set_ref, actor_id, found_items, correction_prompt_ref
        )

    async def check_diagnosis(self, diagnosis_id: str):
        return await self._store.check_diagnosis(diagnosis_id)
