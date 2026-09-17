# B3. Rubric / Rater Service — SC08 — конкретная реализация поверх SQLite.
# Источник инварианта: docs/Модули фреймворка — приоритет.md, раздел B3 —
# append-only (та же логика, что защищает Attempt в B2), ровно один активный
# CriterionPackage на domain, get_active_package() никогда не видит DRAFT.

from __future__ import annotations

import json
import sqlite3
import uuid
from asyncio import to_thread

from botkit.rubric.base import Criterion, CriterionPackage, RaterRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS criterion_packages (
    package_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    version INTEGER NOT NULL,
    dimensions_json TEXT NOT NULL,
    owner_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_packages_domain
    ON criterion_packages(domain, version);

CREATE TABLE IF NOT EXISTS rater_records (
    rater_record_id TEXT PRIMARY KEY,
    target_attempt_id TEXT NOT NULL,
    package_ref TEXT NOT NULL REFERENCES criterion_packages(package_id),
    criterion_version INTEGER NOT NULL,
    rater_id TEXT NOT NULL,
    rater_qualification TEXT,
    labels_found_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ratings_target
    ON rater_records(target_attempt_id);
"""


class UnknownPackageError(Exception):
    """activate_package()/record_rating() ссылаются на package_id, которого
    нет в хранилище — ошибка конфигурации вызывающего кода, не runtime-
    состояние, которое стоит проглатывать."""


class NoActivePackageError(Exception):
    """get_active_package(domain) вызван для domain, где ни один package
    ещё не был activate_package()-нут (только DRAFT/ACCEPTED, или домен
    вообще пуст)."""


def _dimensions_to_json(dimensions: list[Criterion]) -> str:
    return json.dumps([
        {"dimension_id": d.dimension_id, "label": d.label, "anchor_examples": d.anchor_examples}
        for d in dimensions
    ])


def _dimensions_from_json(raw: str) -> list[Criterion]:
    return [Criterion(**d) for d in json.loads(raw)]


def _row_to_package(row: sqlite3.Row) -> CriterionPackage:
    return CriterionPackage(
        package_id=row["package_id"],
        domain=row["domain"],
        version=row["version"],
        dimensions=_dimensions_from_json(row["dimensions_json"]),
        owner_status=row["owner_status"],
    )


def _row_to_rating(row: sqlite3.Row) -> RaterRecord:
    return RaterRecord(
        rater_record_id=row["rater_record_id"],
        target_attempt_id=row["target_attempt_id"],
        package_ref=row["package_ref"],
        criterion_version=row["criterion_version"],
        rater_id=row["rater_id"],
        rater_qualification=row["rater_qualification"],
        labels_found=json.loads(row["labels_found_json"]),
        evidence=json.loads(row["evidence_json"]),
    )


class SQLiteRubricStore:
    """RubricStore поверх SQLite. Один файл = один процесс-writer, как и
    SQLiteAttemptStore (B2) — SQLite сериализует конкурентные записи через
    свой файловый лок, этого достаточно для одного бот-процесса."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def register_package(
        self, domain: str, dimensions: list[Criterion], *, activate: bool = False
    ) -> CriterionPackage:
        return await to_thread(self._register_package_sync, domain, dimensions, activate)

    def _register_package_sync(
        self, domain: str, dimensions: list[Criterion], activate: bool
    ) -> CriterionPackage:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT MAX(version) AS max_version FROM criterion_packages WHERE domain = ?",
                (domain,),
            ).fetchone()
            next_version = (row["max_version"] or 0) + 1

            package = CriterionPackage(
                package_id=str(uuid.uuid4()),
                domain=domain,
                version=next_version,
                dimensions=dimensions,
                owner_status="draft",
            )
            conn.execute(
                """
                INSERT INTO criterion_packages
                    (package_id, domain, version, dimensions_json, owner_status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    package.package_id,
                    package.domain,
                    package.version,
                    _dimensions_to_json(package.dimensions),
                    package.owner_status,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        if activate:
            return self._activate_package_sync(package.package_id)
        return package

    async def activate_package(self, package_id: str) -> CriterionPackage:
        return await to_thread(self._activate_package_sync, package_id)

    def _activate_package_sync(self, package_id: str) -> CriterionPackage:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM criterion_packages WHERE package_id = ?", (package_id,)
            ).fetchone()
            if row is None:
                raise UnknownPackageError(f"no CriterionPackage with package_id={package_id!r}")
            domain = row["domain"]

            conn.execute(
                """
                UPDATE criterion_packages SET owner_status = 'superseded'
                WHERE domain = ? AND owner_status = 'active'
                """,
                (domain,),
            )
            conn.execute(
                "UPDATE criterion_packages SET owner_status = 'active' WHERE package_id = ?",
                (package_id,),
            )
            conn.commit()

            row = conn.execute(
                "SELECT * FROM criterion_packages WHERE package_id = ?", (package_id,)
            ).fetchone()
            return _row_to_package(row)
        finally:
            conn.close()

    async def get_active_package(self, domain: str) -> CriterionPackage:
        return await to_thread(self._get_active_package_sync, domain)

    def _get_active_package_sync(self, domain: str) -> CriterionPackage:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM criterion_packages WHERE domain = ? AND owner_status = 'active'",
                (domain,),
            ).fetchone()
            if row is None:
                raise NoActivePackageError(f"no active CriterionPackage for domain={domain!r}")
            return _row_to_package(row)
        finally:
            conn.close()

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
        return await to_thread(
            self._record_rating_sync,
            target_attempt_id,
            package_ref,
            criterion_version,
            rater_id,
            labels_found,
            evidence,
            rater_qualification,
        )

    def _record_rating_sync(
        self,
        target_attempt_id: str,
        package_ref: str,
        criterion_version: int,
        rater_id: str,
        labels_found: list[str],
        evidence: list[str],
        rater_qualification: str | None,
    ) -> RaterRecord:
        rating = RaterRecord(
            rater_record_id=str(uuid.uuid4()),
            target_attempt_id=target_attempt_id,
            package_ref=package_ref,
            criterion_version=criterion_version,
            rater_id=rater_id,
            rater_qualification=rater_qualification,
            labels_found=labels_found,
            evidence=evidence,
        )
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM criterion_packages WHERE package_id = ?", (package_ref,)
            ).fetchone()
            if row is None:
                raise UnknownPackageError(f"no CriterionPackage with package_id={package_ref!r}")

            conn.execute(
                """
                INSERT INTO rater_records
                    (rater_record_id, target_attempt_id, package_ref, criterion_version,
                     rater_id, rater_qualification, labels_found_json, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rating.rater_record_id,
                    rating.target_attempt_id,
                    rating.package_ref,
                    rating.criterion_version,
                    rating.rater_id,
                    rating.rater_qualification,
                    json.dumps(rating.labels_found),
                    json.dumps(rating.evidence),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return rating

    async def get_ratings(self, target_attempt_id: str) -> list[RaterRecord]:
        return await to_thread(self._get_ratings_sync, target_attempt_id)

    def _get_ratings_sync(self, target_attempt_id: str) -> list[RaterRecord]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM rater_records WHERE target_attempt_id = ?",
                (target_attempt_id,),
            ).fetchall()
            return [_row_to_rating(row) for row in rows]
        finally:
            conn.close()
