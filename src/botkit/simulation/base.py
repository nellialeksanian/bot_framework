# C1. Simulation & Scenario Engine — SC02 + SC03
# Источник: "Модули фреймворка — приоритет.md", раздел C1; поля сверены с
# DS08/DS09 из "Реестр контрактов SC01-SC20.md" (разделы SC02, SC03) и с
# первичными author-TZ материализациями FD42-020 ("ИИ-Эксплуатант") и
# FD42-006 ("Гельминтолог-детектив").
# (а) устойчивая ролевая симуляция с границей знания (SC02),
# (б) генерация контролируемых учебных кейсов/дефектов с provenance (SC03).
#
# Статус источника: challenger draft, приёмка не выполнена (ATF15/ATF16 не
# пройдены) — см. "Модули фреймворка — приоритет.md", раздел C1, таблица
# происхождения во введении. Deep-подтверждение спроса 0/3 для обоих SC —
# 11 потребителей SC02, 8 потребителей SC03 (successor-пересчёт P028/P030:
# FD42-015 снят с SC03 — генерация фабул там оказалась презентационной, не
# runtime-механизмом).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class SimulationRun:
    run_id: str
    # actor_id — прямое поле, не выводится косвенно через attempt_ref.
    # Раньше "чей это run" можно было узнать только через Attempt.actor_id
    # (B2), но attempt_ref не обязателен (см. ниже) — без прямого actor_id
    # get_active_run() не смог бы найти активный run для студента, если у
    # него нет черновика вообще (напр. переговорный тренажёр).
    actor_id: str
    # attempt_ref НЕ обязателен для всего SC02 — только для той его ветви,
    # где симулятор буквально тестирует ЧЕЛОВЕЧЕСКИЙ черновик (FD42-020
    # "ИИ-Эксплуатант": FIRST_DRAFT_GATE, T020-02). Другие потребители SC02
    # из реестра (напр. FD42-007 "Песочница консультанта" — переговорный
    # тренажёр с симулированным клиентом) ведут диалог без предшествующего
    # черновика вообще — там ссылаться не на что, и это не ошибка, а другая
    # форма одного и того же SC02. См. require_attempt в PersonaSimulator.start().
    attempt_ref: str | None
    task_ref: str
    simulator_policy_version: int
    literalness_profile_version: int  # DS08 — буквальность чтения инструкции симулятором (R0105 FD42-020)
    persona_profile_version: int | None
    status: Literal["draft", "started", "completed", "revision_eligible"]
    created_at: datetime


@dataclass
class SimulationTurn:
    """R1001 FD42-020: система обязана логировать итерации — SimulationRun
    сам по себе хранит только факт и статус диалога, без него ни одна
    реплика не восстановима. Пишется автоматически внутри respond()
    (реализацией PersonaSimulator), а не навыком — так же, как BotResponse
    в B2 пишется автоматически внутри record_response(), а не вручную
    навыком после каждого вызова LLM."""

    turn_id: str
    run_ref: str
    turn_index: int  # 0-based порядковый номер хода внутри run — для чтения истории по порядку
    actor_message: str
    persona_response: str
    created_at: datetime


@dataclass
class ExecutionObstacle:
    obstacle_id: str
    run_ref: str
    step_ref: str
    obstacle_type: str
    evidence_ref: str
    message_ref: str | None  # DS08 — ссылка на конкретное сообщение симулятора, породившее препятствие
    advisory_content_present: bool  # True = нарушение роли (совет вместо реакции персоны)
    status: Literal["open", "acknowledged"]


class PersonaSimulator(Protocol):
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
        """require_attempt=True — FD42-020-подобный навык (буквальный тест
        человеческого черновика): attempt_ref обязателен и обязан ссылаться
        на существующий Attempt (R0201/R0304, T020-02 FIRST_DRAFT_GATE) —
        отсутствие черновика DENY_SIMULATION. require_attempt=False (по
        умолчанию) — свободный диалог с персоной без предшествующего
        черновика (напр. переговорный тренажёр); attempt_ref тогда может
        быть None. actor_id всегда обязателен — нужен get_active_run(),
        чтобы найти активный run для студента независимо от того, есть ли
        у него черновик."""
        ...

    async def respond(self, run_id: str, student_message: str) -> str:
        """Никогда не выходит за границу знания персоны и не даёт советов от лица симулятора."""
        ...

    async def complete(self, run_id: str) -> SimulationRun:
        ...

    async def get_active_run(self, actor_id: str, task_ref: str) -> SimulationRun | None:
        """Зеркало ActiveSessionResolver.is_awaiting_choice() из B2 (attempts/sessions.py).

        B1 IntentRouter отвечает только на "какой навык уместен сейчас, при
        первом сообщении" — он не решает "это продолжение уже идущего
        stateful-диалога". Пока роль активна (status == "started"), входящее
        сообщение обязано идти напрямую в respond(), минуя route() — иначе
        реплика внутри симуляции классифицировалась бы IntentRouter'ом как
        обычный вопрос к боту, а не как часть уже идущей ролевой игры.
        Вызывающий код (бот) проверяет get_active_run() ДО route(), как
        is_awaiting_choice() для B2."""
        ...

    async def get_turns(self, run_id: str) -> list[SimulationTurn]:
        """R1001 FD42-020: реконструируемая история диалога, root-first
        (turn_index по возрастанию) — как get_lineage() в B2 восстанавливает
        цепочку ревизий."""
        ...


@dataclass
class DefectSet:
    defect_set_id: str
    task_ref: str
    source_standard_ref: str
    generator_policy_version: int
    model_config_version: str  # DS09 — конфигурация модели/промпта, зафиксированная при генерации (R0205 FD42-006)
    intended_defect_count: int
    # R0301 FD42-006: "интервенция SHALL представить студенту намеренно
    # ошибочный текст как объект критического анализа" — сам текст-стимул,
    # не только структурированный список ошибок. Без этого поля студенту
    # физически нечего показать: defects[] описывает ошибки, но не содержит
    # текста, в который они вставлены.
    stimulus_text: str
    defects: list[dict]
    guard_status: Literal["draft", "validated", "released"]


@dataclass
class StudentDiagnosis:
    diagnosis_id: str
    defect_set_ref: str
    actor_id: str
    found_items: list[str]
    correction_prompt_ref: str | None  # DS09 — ссылка на WC05 "teach-back" корректирующий промпт студента
    created_at: datetime


@dataclass
class DiagnosisScore:
    """Результат сверки StudentDiagnosis.found_items с реальными defect_id
    внутри DefectSet.defects — сама запись StudentDiagnosis не содержит
    оценки (T006-06/R0605: machine score остаётся отдельным candidate, не
    вписывается в тот же append-only факт попытки студента)."""

    diagnosis_id: str
    correct_defect_ids: list[str]  # found_items, реально присутствующие в defects
    missed_defect_ids: list[str]  # defect_id из defects, которые студент не назвал
    false_positive_items: list[str]  # found_items, не соответствующие ни одному defect_id


class ScenarioGenerator(Protocol):
    async def generate_defect_set(
        self,
        task_ref: str,
        source_standard_ref: str,
        intended_defect_count: int,
        model_config_version: str,
    ) -> DefectSet:
        """R0201-R0204 FD42-006: заявленное intended_defect_count обязано
        совпасть с фактическим len(defects) до выхода из draft — расхождение
        блокирует set, а не молча проходит (T006-02 DEFECT_COUNT_MISMATCH)."""
        ...

    async def record_diagnosis(
        self,
        defect_set_ref: str,
        actor_id: str,
        found_items: list[str],
        correction_prompt_ref: str | None = None,
    ) -> StudentDiagnosis:
        """R0406 FD42-006: defect_set_ref обязан указывать на set с
        guard_status="released" — diagnosis на draft/validated — orphan (T006-05)."""
        ...

    async def check_diagnosis(self, diagnosis_id: str) -> DiagnosisScore:
        """Сверяет StudentDiagnosis.found_items с реальными defect_id внутри
        связанного DefectSet.defects — не вызывается автоматически внутри
        record_diagnosis(), решение раскрыть результат остаётся у вызывающего
        навыка (напр. можно показать студенту только после нескольких попыток,
        не сразу)."""
        ...
