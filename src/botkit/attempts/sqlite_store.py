# B2. Attempt & Revision Store — SC07 — конкретная реализация поверх SQLite.
# Источник инварианта: docs/Модули фреймворка — приоритет.md, раздел B2 —
# "хранилище по умолчанию — не in-memory dict, а что-то переживающее рестарт
# процесса (минимум SQLite)", закрывает баг потери данных при рестарте,
# который был в gb_analytical_bot (in-memory dict).

from __future__ import annotations

import sqlite3
import uuid
from asyncio import to_thread
from datetime import datetime, timezone
from hashlib import sha256

from botkit.attempts.base import Attempt, BotResponse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    task_ref TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    origin TEXT NOT NULL,
    parent_attempt_id TEXT REFERENCES attempts(attempt_id)
);
CREATE INDEX IF NOT EXISTS idx_attempts_actor_task
    ON attempts(actor_id, task_ref, created_at);

CREATE TABLE IF NOT EXISTS bot_responses (
    response_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES attempts(attempt_id),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    skill_context TEXT,
    support_policy_ref TEXT,
    llm_response_ref TEXT
);
"""


class DuplicateResponseError(Exception):
    """record_response() вызван второй раз для того же attempt_id.

    Инвариант B2: Attempt <-> BotResponse строго 1:1 — ретраи LLM (A1)
    разрешаются внутри генерации ответа, до этого вызова, а не как
    несколько BotResponse на один attempt.
    """


def _row_to_attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(
        attempt_id=row["attempt_id"],
        actor_id=row["actor_id"],
        task_ref=row["task_ref"],
        content=row["content"],
        content_hash=row["content_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
        origin=row["origin"],
        parent_attempt_id=row["parent_attempt_id"],
    )


def _row_to_response(row: sqlite3.Row) -> BotResponse:
    return BotResponse(
        response_id=row["response_id"],
        attempt_id=row["attempt_id"],
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
        skill_context=row["skill_context"],
        support_policy_ref=row["support_policy_ref"],
        llm_response_ref=row["llm_response_ref"],
    )


class SQLiteAttemptStore:
    """AttemptStore поверх SQLite. Один файл = один процесс-writer

    (SQLite сериализует конкурентные записи через свой файловый лок — этого
    достаточно для одного бот-процесса; шардировать на несколько writer'ов
    сюда не входит).
    """

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

    async def record(
        self, actor_id: str, task_ref: str, content: str, parent_id: str | None
    ) -> Attempt:
        return await to_thread(self._record_sync, actor_id, task_ref, content, parent_id)

    def _record_sync(
        self, actor_id: str, task_ref: str, content: str, parent_id: str | None
    ) -> Attempt:
        attempt = Attempt(
            attempt_id=str(uuid.uuid4()),
            actor_id=actor_id,
            task_ref=task_ref,
            content=content,
            content_hash=sha256(content.encode("utf-8")).hexdigest(),
            created_at=datetime.now(timezone.utc),
            origin="HUMAN",
            parent_attempt_id=parent_id,
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO attempts
                    (attempt_id, actor_id, task_ref, content, content_hash,
                     created_at, origin, parent_attempt_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.attempt_id,
                    attempt.actor_id,
                    attempt.task_ref,
                    attempt.content,
                    attempt.content_hash,
                    attempt.created_at.isoformat(),
                    attempt.origin,
                    attempt.parent_attempt_id,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return attempt

    async def get_lineage(self, attempt_id: str) -> list[Attempt]:
        return await to_thread(self._get_lineage_sync, attempt_id)

    def _get_lineage_sync(self, attempt_id: str) -> list[Attempt]:
        conn = self._connect()
        try:
            chain: list[Attempt] = []
            current_id: str | None = attempt_id
            while current_id is not None:
                row = conn.execute(
                    "SELECT * FROM attempts WHERE attempt_id = ?", (current_id,)
                ).fetchone()
                if row is None:
                    break
                attempt = _row_to_attempt(row)
                chain.append(attempt)
                current_id = attempt.parent_attempt_id
            chain.reverse()  # root-first, как задокументировано в B2 ("вся цепочка от корня")
            return chain
        finally:
            conn.close()

    async def latest(self, actor_id: str, task_ref: str) -> Attempt | None:
        return await to_thread(self._latest_sync, actor_id, task_ref)

    def _latest_sync(self, actor_id: str, task_ref: str) -> Attempt | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM attempts
                WHERE actor_id = ? AND task_ref = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (actor_id, task_ref),
            ).fetchone()
            return _row_to_attempt(row) if row is not None else None
        finally:
            conn.close()

    async def record_response(
        self,
        attempt_id: str,
        content: str,
        *,
        skill_context: str | None = None,
        support_policy_ref: str | None = None,
        llm_response_ref: str | None = None,
    ) -> BotResponse:
        return await to_thread(
            self._record_response_sync,
            attempt_id,
            content,
            skill_context,
            support_policy_ref,
            llm_response_ref,
        )

    def _record_response_sync(
        self,
        attempt_id: str,
        content: str,
        skill_context: str | None,
        support_policy_ref: str | None,
        llm_response_ref: str | None,
    ) -> BotResponse:
        response = BotResponse(
            response_id=str(uuid.uuid4()),
            attempt_id=attempt_id,
            content=content,
            created_at=datetime.now(timezone.utc),
            skill_context=skill_context,
            support_policy_ref=support_policy_ref,
            llm_response_ref=llm_response_ref,
        )
        conn = self._connect()
        try:
            try:
                conn.execute(
                    """
                    INSERT INTO bot_responses
                        (response_id, attempt_id, content, created_at,
                         skill_context, support_policy_ref, llm_response_ref)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        response.response_id,
                        response.attempt_id,
                        response.content,
                        response.created_at.isoformat(),
                        response.skill_context,
                        response.support_policy_ref,
                        response.llm_response_ref,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateResponseError(
                    f"BotResponse already recorded for attempt_id={attempt_id!r}"
                ) from exc
            conn.commit()
        finally:
            conn.close()
        return response

    async def get_response(self, attempt_id: str) -> BotResponse | None:
        return await to_thread(self._get_response_sync, attempt_id)

    def _get_response_sync(self, attempt_id: str) -> BotResponse | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM bot_responses WHERE attempt_id = ?", (attempt_id,)
            ).fetchone()
            return _row_to_response(row) if row is not None else None
        finally:
            conn.close()
