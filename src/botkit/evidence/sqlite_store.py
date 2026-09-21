# B5. Evidence & Citation Layer — SC05 — конкретная реализация EvidenceStore
# поверх SQLite. Источник инварианта: docs/Модули фреймворка — приоритет.md,
# раздел B5 — append-only (та же логика, что защищает Attempt в B2 и
# RaterRecord в B3): повторная проверка claim/chunk_ref добавляет новую
# EvidenceLink, не переписывает предыдущую.

from __future__ import annotations

import sqlite3
import uuid
from asyncio import to_thread
from datetime import datetime, timezone

from botkit.evidence.base import EvidenceLink

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_links (
    evidence_link_id TEXT PRIMARY KEY,
    claim TEXT NOT NULL,
    chunk_ref TEXT NOT NULL,
    source TEXT NOT NULL,
    entailment TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_claim
    ON evidence_links(claim);
CREATE INDEX IF NOT EXISTS idx_evidence_claim_entailment
    ON evidence_links(claim, entailment);
"""


def _row_to_link(row: sqlite3.Row) -> EvidenceLink:
    return EvidenceLink(
        evidence_link_id=row["evidence_link_id"],
        claim=row["claim"],
        chunk_ref=row["chunk_ref"],
        source=row["source"],
        entailment=row["entailment"],
        created_at=row["created_at"],
    )


class SQLiteEvidenceStore:
    """EvidenceStore поверх SQLite. Один файл = один процесс-writer, как и
    SQLiteRubricStore (B3) и SQLiteAttemptStore (B2) — SQLite сериализует
    конкурентные записи через свой файловый лок, этого достаточно для
    одного бот-процесса."""

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
        return conn

    async def record(self, link: EvidenceLink) -> EvidenceLink:
        return await to_thread(self._record_sync, link)

    def _record_sync(self, link: EvidenceLink) -> EvidenceLink:
        stored = EvidenceLink(
            evidence_link_id=link.evidence_link_id or str(uuid.uuid4()),
            claim=link.claim,
            chunk_ref=link.chunk_ref,
            source=link.source,
            entailment=link.entailment,
            created_at=link.created_at or datetime.now(timezone.utc).isoformat(),
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO evidence_links
                    (evidence_link_id, claim, chunk_ref, source, entailment, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.evidence_link_id,
                    stored.claim,
                    stored.chunk_ref,
                    stored.source,
                    stored.entailment,
                    stored.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return stored

    async def get_for_claim(self, claim: str) -> list[EvidenceLink]:
        return await to_thread(self._get_for_claim_sync, claim)

    def _get_for_claim_sync(self, claim: str) -> list[EvidenceLink]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM evidence_links WHERE claim = ? ORDER BY created_at",
                (claim,),
            ).fetchall()
            return [_row_to_link(row) for row in rows]
        finally:
            conn.close()

    async def get_conflicting(self, claim: str) -> list[EvidenceLink]:
        return await to_thread(self._get_conflicting_sync, claim)

    def _get_conflicting_sync(self, claim: str) -> list[EvidenceLink]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM evidence_links
                WHERE claim = ? AND entailment = 'CONFLICTING'
                ORDER BY created_at
                """,
                (claim,),
            ).fetchall()
            return [_row_to_link(row) for row in rows]
        finally:
            conn.close()
