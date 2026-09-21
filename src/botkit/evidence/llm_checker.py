# B5. Evidence & Citation Layer — SC04 + SC05 — LLM-реализация
# EvidenceChecker поверх A1 (LLMProvider).
#
# Тот же приём, что LLMRevisionClassifier (B2, attempts/sessions.py) и
# LLMRoleBoundaryClassifier (C1, simulation/persona.py): отдельный, более
# дешёвый/быстрый LLM-вызов ПОСЛЕ того, как ответ уже сформирован, а не
# просьба внутри основного промпта "не выдумывай источник" — та просьба
# ничего не проверяет постфактум, полагается на добросовестность модели.
#
# Fail-closed по инварианту 5.7 карты v2.2 ("Grounding: citation не равна
# evidence"): неразборчивый ответ классификатора -> UNKNOWN, а не угадывание
# в сторону SUPPORTED. UNKNOWN — легитимный результат, не ошибка, которую
# нужно скрыть или додумать (см. base.py).

from __future__ import annotations

import re

from botkit.llm.base import LLMProvider
from botkit.evidence.base import Entailment, EvidenceLink


async def _invoke_text(llm: LLMProvider, prompt: str) -> str:
    """LLMProvider.ainvoke() по контракту A1 возвращает LLMResponse, но
    некоторые живые боты пока оборачивают легаси-клиент, чей .ainvoke()
    отдаёт str напрямую (см. тот же приём в LLMRevisionClassifier) —
    принимаем оба варианта."""
    response = await llm.ainvoke(prompt)
    if isinstance(response, str):
        return response
    return getattr(response, "response", None) or getattr(response, "raw", "") or ""


_ENTAILMENT_PATTERN = re.compile(
    r'"entailment"\s*:\s*"(SUPPORTED|PARTIAL|CONFLICTING|UNKNOWN)"', re.IGNORECASE
)


def _parse_entailment(raw: str) -> Entailment:
    match = _ENTAILMENT_PATTERN.search(raw)
    if match is None:
        return "UNKNOWN"  # не удалось распарсить -> fail-closed, не гадаем
    return match.group(1).upper()  # type: ignore[return-value]


class LLMEvidenceChecker:
    """EvidenceChecker (B5) поверх LLMProvider (A1) — не заводит отдельный
    LLM-клиент, переиспользует тот же провайдер, что и остальные навыки.

    verify() не пишет в EvidenceStore сама — только классифицирует claim
    против одного chunk и возвращает EvidenceLink с сгенерированным id.
    Запись — отдельный шаг (см. verify_and_record() в base.py), чтобы
    EvidenceChecker оставался stateless и тестируемым без БД, как
    LLMRevisionClassifier и LLMRoleBoundaryClassifier."""

    def __init__(self, llm: LLMProvider) -> None:
        self._llm = llm

    async def verify(self, claim: str, chunk) -> EvidenceLink:
        prompt = f"""
Ты проверяешь, поддерживает ли фрагмент источника утверждение (claim), а не
просто оказался с ним рядом по теме.

Утверждение:
{claim}

Фрагмент источника:
{chunk.text}

Определи состояние:
- SUPPORTED — фрагмент прямо подтверждает утверждение
- PARTIAL — фрагмент подтверждает часть утверждения, но не целиком, или
  требует дополнительного контекста
- CONFLICTING — фрагмент противоречит утверждению
- UNKNOWN — по фрагменту невозможно уверенно определить отношение к
  утверждению (в том числе если фрагмент просто на ту же тему, но не
  содержит прямого основания для утверждения)

Ответь СТРОГО в формате JSON, без markdown:
{{"entailment": "SUPPORTED"}}
""".strip()

        raw = await _invoke_text(self._llm, prompt)
        entailment = _parse_entailment(raw)

        return EvidenceLink(
            evidence_link_id="",  # генерируется в EvidenceStore.record()
            claim=claim,
            chunk_ref=f"{chunk.source}::{chunk.chunk_index}",
            source=chunk.source,
            entailment=entailment,
            created_at="",  # генерируется в EvidenceStore.record()
        )
