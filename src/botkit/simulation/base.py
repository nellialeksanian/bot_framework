# C1. Simulation & Scenario Engine — SC02 + SC03
# Источник: "Модули фреймворка — приоритет.md", раздел C1.
# (а) устойчивая ролевая симуляция с границей знания (SC02),
# (б) генерация контролируемых учебных кейсов/дефектов с provenance (SC03).

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol


@dataclass
class SimulationRun:
    run_id: str
    attempt_ref: str
    task_ref: str
    simulator_policy_version: int
    persona_profile_version: int | None
    status: Literal["draft", "started", "completed", "revision_eligible"]
    created_at: datetime


@dataclass
class ExecutionObstacle:
    obstacle_id: str
    run_ref: str
    step_ref: str
    obstacle_type: str
    evidence_ref: str
    advisory_content_present: bool  # True = нарушение роли (совет вместо реакции персоны)
    status: Literal["open", "acknowledged"]


class PersonaSimulator(Protocol):
    async def start(self, task_ref: str, persona_profile_version: int) -> SimulationRun:
        ...

    async def respond(self, run_id: str, student_message: str) -> str:
        """Никогда не выходит за границу знания персоны и не даёт советов от лица симулятора."""
        ...

    async def complete(self, run_id: str) -> SimulationRun:
        ...


@dataclass
class DefectSet:
    defect_set_id: str
    task_ref: str
    source_standard_ref: str
    generator_policy_version: int
    intended_defect_count: int
    defects: list[dict]
    guard_status: Literal["draft", "validated", "released"]


@dataclass
class StudentDiagnosis:
    diagnosis_id: str
    defect_set_ref: str
    actor_id: str
    found_items: list[str]
    created_at: datetime


class ScenarioGenerator(Protocol):
    async def generate_defect_set(
        self, task_ref: str, source_standard_ref: str, intended_defect_count: int
    ) -> DefectSet:
        ...

    async def record_diagnosis(
        self, defect_set_ref: str, actor_id: str, found_items: list[str]
    ) -> StudentDiagnosis:
        ...
