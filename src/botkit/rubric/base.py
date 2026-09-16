# B3. Rubric & Taxonomy Store — SC08
# Источник: "Модули фреймворка — приоритет.md", раздел B3.
# Единая версионируемая структура критериев оценки и таксономии ошибок.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class Criterion:
    label: str
    anchor_examples: list[str]


@dataclass
class CriterionPackage:
    package_id: str
    version: int
    dimensions: list[Criterion]
    owner_status: Literal["draft", "accepted", "active", "superseded"]


@dataclass
class RaterRecord:
    target_attempt_id: str
    package_ref: str
    labels_found: list[str]
    evidence: list[str]


class TimeRange:
    ...


class TaxonomyDistribution:
    ...


class RubricStore(Protocol):
    async def get_active_package(self, domain: str) -> CriterionPackage:
        ...

    async def record_rating(self, rating: RaterRecord) -> None:
        ...

    async def aggregate(self, domain: str, window: TimeRange) -> TaxonomyDistribution:
        ...
