# B3. Rubric / Rater Service — SC08
# Источник: "Модули фреймворка — приоритет.md", раздел B3.
# Единая версионируемая структура критериев оценки, вместо того чтобы
# каждый навык придумывал свою JSON-схему оценивания.
#
# Первая версия сознательно не реализует multi-rater/adjudication (DISAGREEMENT
# -> ADJUDICATED из первичного контракта LEDGER v1.4/FD42-023/028/031) — при
# разборе портфеля не нашлось ни одного описанного примера, где было бы явно
# указано, кто именно два независимых рейтера и как оценка второго (обычно
# подразумевается человек) технически попадает в систему бота. Схема данных
# (rater_id, rater_qualification, criterion_version отдельно от package_ref)
# уже содержит место под это расширение — см. раздел B3 в приоритетном
# документе для полного разбора.

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass
class Criterion:
    dimension_id: str
    label: str
    anchor_examples: list[str]


@dataclass
class CriterionPackage:
    package_id: str
    domain: str
    version: int
    dimensions: list[Criterion]
    owner_status: Literal["draft", "accepted", "active", "superseded"]


@dataclass
class RaterRecord:
    rater_record_id: str
    target_attempt_id: str
    package_ref: str
    criterion_version: int
    rater_id: str
    rater_qualification: str | None
    labels_found: list[str]
    evidence: list[str]


class TimeRange:
    ...


class TaxonomyDistribution:
    ...


class RubricStore(Protocol):
    async def register_package(
        self, domain: str, dimensions: list[Criterion], *, activate: bool = False
    ) -> CriterionPackage:
        # инвариант: каждый вызов создаёт новую версию (version+1 в рамках
        # domain), никогда не изменяет уже зарегистрированный package —
        # то же append-only правило, что и у Attempt в B2. activate=False по
        # умолчанию: пакет создаётся как "draft", get_active_package() его
        # не увидит, пока кто-то явно не активирует (см. activate_package()).
        ...

    async def activate_package(self, package_id: str) -> CriterionPackage:
        # переводит package.owner_status в "active" и одновременно помечает
        # предыдущий активный пакет этого domain как "superseded" — activный
        # пакет в domain всегда ровно один.
        ...

    async def get_active_package(self, domain: str) -> CriterionPackage:
        ...

    async def record_rating(
        self,
        target_attempt_id: str,
        package_ref: str,
        criterion_version: int,
        rater_id: str,
        labels_found: list[str],
        evidence: list[str],
        *,
        rater_qualification: str | None = None,
    ) -> RaterRecord:
        # инвариант: append-only, как Attempt в B2 — никогда не перезаписывает
        # существующую RaterRecord, только добавляет новую.
        ...

    async def get_ratings(self, target_attempt_id: str) -> list[RaterRecord]:
        ...

    async def aggregate(self, domain: str, window: TimeRange) -> TaxonomyDistribution:
        ...


def render_criteria_block(package: CriterionPackage) -> str:
    """Рендерит dimensions пакета в текст для вставки в промпт навыка —
    инвариант B3: навык не изобретает названия меток текстом в промпте,
    берёт их из CriterionPackage.dimensions. Аналог render_support_block()
    в B1: чистая функция, без LLM-вызова и side effects.

    dimension_id каждого Criterion — это и есть допустимое значение для
    labels_found в RaterRecord (см. record_rating()), поэтому промпт должен
    просить модель возвращать именно dimension_id, не label (label можно
    переформулировать при следующей версии пакета, dimension_id — нет)."""
    lines = [f"## Критерии оценки ({package.domain}, версия {package.version})"]
    for dim in package.dimensions:
        lines.append(f"### {dim.dimension_id}: {dim.label}")
        for example in dim.anchor_examples:
            lines.append(f"- {example}")
    return "\n".join(lines)
