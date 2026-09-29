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

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass
class Criterion:
    dimension_id: str
    label: str
    anchor_examples: list[str]
    # score_scale/scoring_rule — не всякий CriterionPackage использует
    # числовые баллы (labels_found одни уже достаточны для чистой
    # бинарной "нарушен/не нарушен" рубрики) — оба поля опциональны с
    # дефолтом на самую частую шкалу (0/1), не ломают существующие
    # CriterionPackage, созданные до этого расширения.
    #
    # score_scale — явный набор допустимых числовых значений, не
    # max_score+allows_half: разные критерии одного пакета встречаются с
    # разными и не всегда равномерными шкалами (напр. 0/1/2 для одного
    # критерия, 0/0.5/1 для другого в том же пакете — см. живой пример в
    # боте "Путилин", Приложение 1 ТЗ), max_score/allows_half не выразил
    # бы это без дополнительных спецправил.
    score_scale: tuple[float, ...] = (0.0, 1.0)
    # scoring_rule — текст условия "при каком признаке в работе студента
    # какой балл ставится" (напр. "2 балла: ...; 1 балл: ...; 0 баллов:
    # ..."), не то же самое, что anchor_examples (лингвистические маркеры
    # ошибки/её отсутствия) — правило и маркеры дополняют друг друга в
    # промпте, оба нужны модели, чтобы не просто узнать нарушение, но и
    # знать, каким числом его оценить. Пустая строка (дефолт) означает
    # "правило не задано" — render_criteria_block() тогда не выводит его.
    scoring_rule: str = ""


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
    # scores — опционально: {dimension_id: числовой балл}, только для
    # dimension_id из CriterionPackage.dimensions, где Criterion.score_scale
    # реально используется (не (0.0, 1.0) по умолчанию). Пустой dict —
    # легитимное значение для рубрик, где числовые баллы не нужны, labels_found
    # уже достаточно (напр. gb_edu_bot::feedback — бинарная "нарушен/не
    # нарушен" рубрика, никогда не заполняет scores). Не заменяет
    # labels_found — labels_found остаётся тем, что модель ОБЯЗАНА вернуть
    # (какие критерии затронуты), scores — тем, что она возвращает
    # ДОПОЛНИТЕЛЬНО, когда критерий использует более чем двоичную шкалу.
    scores: dict[str, float] = field(default_factory=dict)


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
        scores: dict[str, float] | None = None,
    ) -> RaterRecord:
        # инвариант: append-only, как Attempt в B2 — никогда не перезаписывает
        # существующую RaterRecord, только добавляет новую.
        # scores=None (по умолчанию) -> RaterRecord.scores = {} — рубрики без
        # числовых баллов не обязаны передавать этот параметр вовсе.
        ...

    async def get_ratings(self, target_attempt_id: str) -> list[RaterRecord]:
        ...

    async def aggregate(self, domain: str, window: TimeRange) -> TaxonomyDistribution:
        ...


def _dimensions_equal(a: list[Criterion], b: list[Criterion]) -> bool:
    # Сравнение по содержимому (все поля Criterion), а не по идентичности
    # объектов — код каждый раз создаёт новые Criterion при запуске
    # процесса, объекты никогда не будут одним и тем же объектом.
    # score_scale/scoring_rule включены в сравнение — правка шкалы баллов
    # или правила их выставления это такое же изменение критерия, как
    # правка label, и обязано версионировать пакет так же.
    if len(a) != len(b):
        return False
    return all(
        x.dimension_id == y.dimension_id
        and x.label == y.label
        and x.anchor_examples == y.anchor_examples
        and x.score_scale == y.score_scale
        and x.scoring_rule == y.scoring_rule
        for x, y in zip(a, b)
    )


async def sync_package(store: RubricStore, domain: str, dimensions: list[Criterion]) -> CriterionPackage:
    """Приводит активный CriterionPackage в domain в соответствие с
    dimensions, заданными в коде — без ручного вызова register_package() на
    каждый рестарт процесса и без версионирования там, где критерии не
    менялись.

    get_active_package() у конкретных реализаций RubricStore бросает свой
    собственный exception, когда активного пакета нет (например,
    NoActivePackageError в SQLiteRubricStore) — RubricStore (Protocol) не
    фиксирует его конкретный тип, поэтому здесь ловится Exception широко и
    трактуется как "пакета ещё нет, нужно зарегистрировать первую версию".

    - Активного пакета нет вообще -> регистрирует и активирует первую версию.
    - Активный пакет есть, но его dimensions отличаются от переданных
      (другой набор dimension_id, или изменился label/anchor_examples у
      существующего) -> регистрирует НОВУЮ версию и активирует её; прежняя
      версия становится "superseded" (activate_package() делает это сама),
      не удаляется — вся история критериев остаётся в БД, просто неактивна.
    - Активный пакет уже совпадает по содержимому -> ничего не делает,
      возвращает его как есть (без создания версии-дубликата).

    Вызывается при каждом старте процесса — редактирование критериев
    сводится к правке списка Criterion в коде и рестарту бота, без ручного
    вызова register_package()/activate_package() из отдельного скрипта."""
    try:
        active = await store.get_active_package(domain)
    except Exception:
        active = None

    if active is not None and _dimensions_equal(active.dimensions, dimensions):
        return active

    return await store.register_package(domain, dimensions, activate=True)


def render_criteria_block(package: CriterionPackage) -> str:
    """Рендерит dimensions пакета в текст для вставки в промпт навыка —
    инвариант B3: навык не изобретает названия меток текстом в промпте,
    берёт их из CriterionPackage.dimensions. Аналог render_support_block()
    в B1: чистая функция, без LLM-вызова и side effects.

    dimension_id каждого Criterion — это и есть допустимое значение для
    labels_found в RaterRecord (см. record_rating()), поэтому промпт должен
    просить модель возвращать именно dimension_id, не label (label можно
    переформулировать при следующей версии пакета, dimension_id — нет).

    scoring_rule выводится только если непусто (дефолт "" — легитимное
    "правило не задано" для чисто бинарных labels_found-рубрик, см.
    Criterion). score_scale выводится всегда, когда scoring_rule есть —
    без него правило ссылалось бы на баллы, о допустимости которых модель
    не проинформирована явно."""
    lines = [f"## Критерии оценки ({package.domain}, версия {package.version})"]
    for dim in package.dimensions:
        lines.append(f"### {dim.dimension_id}: {dim.label}")
        for example in dim.anchor_examples:
            lines.append(f"- {example}")
        if dim.scoring_rule:
            scale_text = "/".join(str(s) for s in dim.score_scale)
            lines.append(f"Шкала баллов: {scale_text}. Правило: {dim.scoring_rule}")
    return "\n".join(lines)
