# C1. Simulation & Scenario Engine — SC02 + SC03 — конкретная реализация
# поверх SQLite.
#
# Источник инвариантов: docs/Модули фреймворка — приоритет.md, раздел C1;
# первичные author-TZ материализации FD42-020 ("ИИ-Эксплуатант", SC02) и
# FD42-006 ("Гельминтолог-детектив", SC03).
#
# Статус источника: challenger draft, приёмка не выполнена (ATF15/ATF16 не
# пройдены) — эта реализация не проходит собственный приёмочный тест, она
# закрывает разрыв "интерфейс описан, стора нет вообще", а не подтверждает
# гипотезу деревом deep-разборов.

from __future__ import annotations

import sqlite3
import uuid
from asyncio import to_thread
from datetime import datetime, timezone

import json

from botkit.attempts.base import AttemptStore
from botkit.simulation.base import (
    DefectSet,
    DiagnosisScore,
    ExecutionObstacle,
    SimulationRun,
    SimulationTurn,
    StudentDiagnosis,
)

_SIMULATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id TEXT PRIMARY KEY,
    actor_id TEXT NOT NULL,
    attempt_ref TEXT,
    task_ref TEXT NOT NULL,
    simulator_policy_version INTEGER NOT NULL,
    literalness_profile_version INTEGER NOT NULL,
    persona_profile_version INTEGER,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_attempt
    ON simulation_runs(attempt_ref);
CREATE INDEX IF NOT EXISTS idx_runs_actor_task
    ON simulation_runs(actor_id, task_ref, status);

CREATE TABLE IF NOT EXISTS simulation_turns (
    turn_id TEXT PRIMARY KEY,
    run_ref TEXT NOT NULL REFERENCES simulation_runs(run_id),
    turn_index INTEGER NOT NULL,
    actor_message TEXT NOT NULL,
    persona_response TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_run
    ON simulation_turns(run_ref, turn_index);

CREATE TABLE IF NOT EXISTS execution_obstacles (
    obstacle_id TEXT PRIMARY KEY,
    run_ref TEXT NOT NULL REFERENCES simulation_runs(run_id),
    step_ref TEXT NOT NULL,
    obstacle_type TEXT NOT NULL,
    evidence_ref TEXT NOT NULL,
    message_ref TEXT,
    advisory_content_present INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obstacles_run
    ON execution_obstacles(run_ref);
"""

_SCENARIO_SCHEMA = """
CREATE TABLE IF NOT EXISTS defect_sets (
    defect_set_id TEXT PRIMARY KEY,
    task_ref TEXT NOT NULL,
    source_standard_ref TEXT NOT NULL,
    generator_policy_version INTEGER NOT NULL,
    model_config_version TEXT NOT NULL,
    intended_defect_count INTEGER NOT NULL,
    stimulus_text TEXT NOT NULL,
    defects_json TEXT NOT NULL,
    guard_status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_defect_sets_task
    ON defect_sets(task_ref);

CREATE TABLE IF NOT EXISTS student_diagnoses (
    diagnosis_id TEXT PRIMARY KEY,
    defect_set_ref TEXT NOT NULL REFERENCES defect_sets(defect_set_id),
    actor_id TEXT NOT NULL,
    found_items_json TEXT NOT NULL,
    correction_prompt_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_diagnoses_defect_set
    ON student_diagnoses(defect_set_ref);
"""


class MissingFirstDraftError(Exception):
    """start() вызван с attempt_ref, которого нет в AttemptStore.

    Инвариант T020-02 FIRST_DRAFT_GATE (FD42-020, R0201/R0304): симуляция
    обязана ссылаться на уже существующую человеческую попытку — черновик,
    написанный студентом ДО вмешательства симулятора. Отсутствие такого
    attempt — DENY_SIMULATION, а не тихий запуск run без основания.
    """


class UnknownRunError(Exception):
    """respond()/complete()/record_obstacle() ссылаются на run_id, которого нет."""


class RunNotStartedError(Exception):
    """respond() вызван для run, который ещё не в статусе "started"."""


class DefectCountMismatchError(Exception):
    """generate_defect_set() вызван с intended_defect_count, не равным
    len(defects) в фактически переданном списке.

    Инвариант T006-02 DEFECT_COUNT_MISMATCH (FD42-006, R0201/R0204):
    заявленное число намеренных дефектов обязано совпадать с фактическим
    ДО того, как набор станет виден студенту — расхождение (лишний или
    недостающий дефект) блокирует запись, а не тихо проходит с неверным
    intended_defect_count.
    """


class UnknownDefectSetError(Exception):
    """validate_defect_set()/release_defect_set()/record_diagnosis() ссылаются
    на defect_set_id, которого нет в хранилище."""


class UnknownDiagnosisError(Exception):
    """check_diagnosis() вызван с diagnosis_id, которого нет в хранилище."""


class GuardNotReleasedError(Exception):
    """record_diagnosis() вызван для defect_set, чей guard_status != "released".

    Инвариант T006-04/T006-05 (FD42-006, R0302-R0304): сгенерированный набор с
    эталонным ключом не выдаётся студенту напрямую из "draft"/"validated" —
    ALLOW/DENY/UNKNOWN/REVIEW_REQUIRED не конвертируются в ALLOW тихим
    runtime-фолбэком, только явным release_defect_set() после guard-проверки.
    """


def _row_to_run(row: sqlite3.Row) -> SimulationRun:
    return SimulationRun(
        run_id=row["run_id"],
        actor_id=row["actor_id"],
        attempt_ref=row["attempt_ref"],
        task_ref=row["task_ref"],
        simulator_policy_version=row["simulator_policy_version"],
        literalness_profile_version=row["literalness_profile_version"],
        persona_profile_version=row["persona_profile_version"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_defect_set(row: sqlite3.Row) -> DefectSet:
    return DefectSet(
        defect_set_id=row["defect_set_id"],
        task_ref=row["task_ref"],
        source_standard_ref=row["source_standard_ref"],
        generator_policy_version=row["generator_policy_version"],
        model_config_version=row["model_config_version"],
        intended_defect_count=row["intended_defect_count"],
        stimulus_text=row["stimulus_text"],
        defects=json.loads(row["defects_json"]),
        guard_status=row["guard_status"],
    )


def _row_to_diagnosis(row: sqlite3.Row) -> StudentDiagnosis:
    return StudentDiagnosis(
        diagnosis_id=row["diagnosis_id"],
        defect_set_ref=row["defect_set_ref"],
        actor_id=row["actor_id"],
        found_items=json.loads(row["found_items_json"]),
        correction_prompt_ref=row["correction_prompt_ref"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_obstacle(row: sqlite3.Row) -> ExecutionObstacle:
    return ExecutionObstacle(
        obstacle_id=row["obstacle_id"],
        run_ref=row["run_ref"],
        step_ref=row["step_ref"],
        obstacle_type=row["obstacle_type"],
        evidence_ref=row["evidence_ref"],
        message_ref=row["message_ref"],
        advisory_content_present=bool(row["advisory_content_present"]),
        status=row["status"],
    )


def _row_to_turn(row: sqlite3.Row) -> SimulationTurn:
    return SimulationTurn(
        turn_id=row["turn_id"],
        run_ref=row["run_ref"],
        turn_index=row["turn_index"],
        actor_message=row["actor_message"],
        persona_response=row["persona_response"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class SQLiteSimulationLog:
    """Persistence-слой SC02 поверх SQLite — SimulationRun + SimulationTurn + ExecutionObstacle.

    Не реализует PersonaSimulator.respond() из base.py: это, как и в B2/B3,
    чистый persistence без LLM внутри. Фактический вызов модели в роли
    персоны — ответственность навыка (см. A1 LLM Provider Gateway); навык
    вызывает LLMProvider.ainvoke() и затем пишет результат сюда через
    record_obstacle(), если respond обнаружил role-boundary violation.
    Даёт структуру вокруг произвольного respond-вызова: FIRST_DRAFT_GATE
    перед стартом, ExecutionObstacle.advisory_content_present как явный
    маркер нарушения роли (R0404 FD42-020) вместо незамеченного дрейфа
    внутри свободного текста ответа.
    """

    def __init__(self, db_path: str, attempt_store: AttemptStore | None = None):
        # attempt_store не обязателен — нужен только тем навыкам, что вызывают
        # start(require_attempt=True) (FD42-020-подобный "буквальный тест
        # черновика"). Навык без предшествующего черновика (напр. переговорный
        # тренажёр) может создать SQLiteSimulationLog(db_path) без него.
        self._db_path = db_path
        self._attempts = attempt_store
        conn = self._connect()
        try:
            conn.executescript(_SIMULATION_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

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
        if require_attempt:
            # T020-02 FIRST_DRAFT_GATE — только для навыков, которые сами об
            # этом просят (FD42-020-подобный "буквальный тест черновика").
            if attempt_ref is None:
                raise MissingFirstDraftError(
                    "require_attempt=True but attempt_ref is None; a human "
                    "draft must exist before a SimulationRun can start (T020-02)"
                )
            if self._attempts is None:
                raise MissingFirstDraftError(
                    "require_attempt=True but this SQLiteSimulationLog was "
                    "constructed without an attempt_store — cannot verify the "
                    "draft exists (T020-02)"
                )
            lineage = await self._attempts.get_lineage(attempt_ref)
            if not lineage:
                raise MissingFirstDraftError(
                    f"no Attempt with attempt_id={attempt_ref!r}; a human draft "
                    "must exist before a SimulationRun can start (T020-02)"
                )
        return await to_thread(
            self._start_sync,
            actor_id,
            attempt_ref,
            task_ref,
            simulator_policy_version,
            literalness_profile_version,
            persona_profile_version,
        )

    def _start_sync(
        self,
        actor_id: str,
        attempt_ref: str | None,
        task_ref: str,
        simulator_policy_version: int,
        literalness_profile_version: int,
        persona_profile_version: int | None,
    ) -> SimulationRun:
        run = SimulationRun(
            run_id=str(uuid.uuid4()),
            actor_id=actor_id,
            attempt_ref=attempt_ref,
            task_ref=task_ref,
            simulator_policy_version=simulator_policy_version,
            literalness_profile_version=literalness_profile_version,
            persona_profile_version=persona_profile_version,
            status="started",
            created_at=datetime.now(timezone.utc),
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO simulation_runs
                    (run_id, actor_id, attempt_ref, task_ref, simulator_policy_version,
                     literalness_profile_version, persona_profile_version,
                     status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.actor_id,
                    run.attempt_ref,
                    run.task_ref,
                    run.simulator_policy_version,
                    run.literalness_profile_version,
                    run.persona_profile_version,
                    run.status,
                    run.created_at.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return run

    async def get_run(self, run_id: str) -> SimulationRun:
        return await to_thread(self._get_run_sync, run_id)

    def _get_run_sync(self, run_id: str) -> SimulationRun:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise UnknownRunError(f"no SimulationRun with run_id={run_id!r}")
            return _row_to_run(row)
        finally:
            conn.close()

    async def get_active_run(self, actor_id: str, task_ref: str) -> SimulationRun | None:
        """Зеркало ActiveSessionResolver.is_awaiting_choice() из B2 — точка
        входа для обхода B1 route(), пока роль ещё активна (см. "Интеграция
        с B1" в docs/Модули фреймворка — приоритет.md, раздел C1)."""
        return await to_thread(self._get_active_run_sync, actor_id, task_ref)

    def _get_active_run_sync(self, actor_id: str, task_ref: str) -> SimulationRun | None:
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT * FROM simulation_runs
                WHERE actor_id = ? AND task_ref = ? AND status = 'started'
                ORDER BY created_at DESC LIMIT 1
                """,
                (actor_id, task_ref),
            ).fetchone()
            return _row_to_run(row) if row is not None else None
        finally:
            conn.close()

    async def complete(self, run_id: str) -> SimulationRun:
        return await to_thread(self._complete_sync, run_id)

    def _complete_sync(self, run_id: str) -> SimulationRun:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM simulation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise UnknownRunError(f"no SimulationRun with run_id={run_id!r}")
            conn.execute(
                "UPDATE simulation_runs SET status = 'completed' WHERE run_id = ?",
                (run_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM simulation_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            return _row_to_run(row)
        finally:
            conn.close()

    async def record_obstacle(
        self,
        run_ref: str,
        step_ref: str,
        obstacle_type: str,
        evidence_ref: str,
        *,
        message_ref: str | None = None,
        advisory_content_present: bool = False,
    ) -> ExecutionObstacle:
        """R0403 FD42-020: каждое препятствие обязано иметь step_ref/evidence_ref
        (T020-04 OBSTACLE_LOCATOR) — generic criticism без locator сюда не пишется,
        это ответственность вызывающего кода (навыка) отклонить его раньше.
        advisory_content_present=True — структурный маркер role-boundary
        violation (R0404), не стилистическая деталь; обязателен для UI/дашборда,
        который не должен молча пропускать нарушение роли как обычный ответ.
        """
        return await to_thread(
            self._record_obstacle_sync,
            run_ref,
            step_ref,
            obstacle_type,
            evidence_ref,
            message_ref,
            advisory_content_present,
        )

    def _record_obstacle_sync(
        self,
        run_ref: str,
        step_ref: str,
        obstacle_type: str,
        evidence_ref: str,
        message_ref: str | None,
        advisory_content_present: bool,
    ) -> ExecutionObstacle:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM simulation_runs WHERE run_id = ?", (run_ref,)
            ).fetchone()
            if row is None:
                raise UnknownRunError(f"no SimulationRun with run_id={run_ref!r}")

            obstacle = ExecutionObstacle(
                obstacle_id=str(uuid.uuid4()),
                run_ref=run_ref,
                step_ref=step_ref,
                obstacle_type=obstacle_type,
                evidence_ref=evidence_ref,
                message_ref=message_ref,
                advisory_content_present=advisory_content_present,
                status="open",
            )
            conn.execute(
                """
                INSERT INTO execution_obstacles
                    (obstacle_id, run_ref, step_ref, obstacle_type, evidence_ref,
                     message_ref, advisory_content_present, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    obstacle.obstacle_id,
                    obstacle.run_ref,
                    obstacle.step_ref,
                    obstacle.obstacle_type,
                    obstacle.evidence_ref,
                    obstacle.message_ref,
                    int(obstacle.advisory_content_present),
                    obstacle.status,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return obstacle

    async def get_obstacles(self, run_ref: str) -> list[ExecutionObstacle]:
        return await to_thread(self._get_obstacles_sync, run_ref)

    def _get_obstacles_sync(self, run_ref: str) -> list[ExecutionObstacle]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM execution_obstacles WHERE run_ref = ?", (run_ref,)
            ).fetchall()
            return [_row_to_obstacle(row) for row in rows]
        finally:
            conn.close()

    async def record_turn(self, run_ref: str, actor_message: str, persona_response: str) -> SimulationTurn:
        """R1001 FD42-020: пишется автоматически внутри respond() реализации
        PersonaSimulator (см. LLMPersonaSimulator в persona.py) — не
        вызывается навыком напрямую, как и record_response() в B2 пишется
        внутри логики генерации ответа, а не отдельным вызовом из навыка.
        Append-only, как Attempt/RaterRecord — turn_index растёт монотонно
        на run_ref, старые ходы не перезаписываются и не удаляются."""
        return await to_thread(self._record_turn_sync, run_ref, actor_message, persona_response)

    def _record_turn_sync(self, run_ref: str, actor_message: str, persona_response: str) -> SimulationTurn:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM simulation_runs WHERE run_id = ?", (run_ref,)
            ).fetchone()
            if row is None:
                raise UnknownRunError(f"no SimulationRun with run_id={run_ref!r}")

            next_index_row = conn.execute(
                "SELECT COALESCE(MAX(turn_index), -1) + 1 AS next_index FROM simulation_turns WHERE run_ref = ?",
                (run_ref,),
            ).fetchone()
            turn = SimulationTurn(
                turn_id=str(uuid.uuid4()),
                run_ref=run_ref,
                turn_index=next_index_row["next_index"],
                actor_message=actor_message,
                persona_response=persona_response,
                created_at=datetime.now(timezone.utc),
            )
            conn.execute(
                """
                INSERT INTO simulation_turns
                    (turn_id, run_ref, turn_index, actor_message, persona_response, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    turn.turn_id,
                    turn.run_ref,
                    turn.turn_index,
                    turn.actor_message,
                    turn.persona_response,
                    turn.created_at.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return turn

    async def get_turns(self, run_id: str) -> list[SimulationTurn]:
        return await to_thread(self._get_turns_sync, run_id)

    def _get_turns_sync(self, run_id: str) -> list[SimulationTurn]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM simulation_turns WHERE run_ref = ? ORDER BY turn_index ASC",
                (run_id,),
            ).fetchall()
            return [_row_to_turn(row) for row in rows]
        finally:
            conn.close()


class SQLiteScenarioGenerator:
    """ScenarioGenerator (SC03) поверх SQLite — DefectSet + StudentDiagnosis.

    Как и SQLiteSimulationLog, не вызывает LLM: generate_defect_set() пишет
    уже сгенерированный набор дефектов (defects передаёт вызывающий навык,
    получивший их от LLMProvider из A1), а сам стор отвечает за то, чтобы
    заявленное и фактическое число дефектов совпадали (T006-02) и чтобы
    набор с ключом ответа не утёк студенту раньше guard-проверки (T006-04).
    """

    def __init__(self, db_path: str):
        self._db_path = db_path
        conn = self._connect()
        try:
            conn.executescript(_SCENARIO_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    async def generate_defect_set(
        self,
        task_ref: str,
        source_standard_ref: str,
        intended_defect_count: int,
        model_config_version: str,
        *,
        generator_policy_version: int,
        stimulus_text: str,
        defects: list[dict],
    ) -> DefectSet:
        # T006-02 DEFECT_COUNT_MISMATCH — заявленное число обязано совпасть
        # с фактическим ДО записи, не после.
        if len(defects) != intended_defect_count:
            raise DefectCountMismatchError(
                f"intended_defect_count={intended_defect_count} but got "
                f"{len(defects)} defects"
            )
        return await to_thread(
            self._generate_defect_set_sync,
            task_ref,
            source_standard_ref,
            intended_defect_count,
            model_config_version,
            generator_policy_version,
            stimulus_text,
            defects,
        )

    def _generate_defect_set_sync(
        self,
        task_ref: str,
        source_standard_ref: str,
        intended_defect_count: int,
        model_config_version: str,
        generator_policy_version: int,
        stimulus_text: str,
        defects: list[dict],
    ) -> DefectSet:
        # defect_id — стабильная позиционная метка (defect-0, defect-1, ...),
        # присвоенная здесь, а не взятая из ответа LLM: модель не обязана
        # придумывать устойчивый идентификатор, и не должна — без этого
        # StudentDiagnosis.found_items ссылался бы на "defect-N", придуманное
        # вызывающим кодом на честном слове, без способа сверить это с
        # реальным содержимым defects[] (см. check_diagnosis() ниже).
        indexed_defects = [{**d, "defect_id": f"defect-{i}"} for i, d in enumerate(defects)]

        defect_set = DefectSet(
            defect_set_id=str(uuid.uuid4()),
            task_ref=task_ref,
            source_standard_ref=source_standard_ref,
            generator_policy_version=generator_policy_version,
            model_config_version=model_config_version,
            intended_defect_count=intended_defect_count,
            stimulus_text=stimulus_text,
            defects=indexed_defects,
            guard_status="draft",
        )
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO defect_sets
                    (defect_set_id, task_ref, source_standard_ref,
                     generator_policy_version, model_config_version,
                     intended_defect_count, stimulus_text, defects_json, guard_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    defect_set.defect_set_id,
                    defect_set.task_ref,
                    defect_set.source_standard_ref,
                    defect_set.generator_policy_version,
                    defect_set.model_config_version,
                    defect_set.intended_defect_count,
                    defect_set.stimulus_text,
                    json.dumps(defect_set.defects),
                    defect_set.guard_status,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return defect_set

    async def get_defect_set(self, defect_set_id: str) -> DefectSet:
        return await to_thread(self._get_defect_set_sync, defect_set_id)

    def _get_defect_set_sync(self, defect_set_id: str) -> DefectSet:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM defect_sets WHERE defect_set_id = ?", (defect_set_id,)
            ).fetchone()
            if row is None:
                raise UnknownDefectSetError(
                    f"no DefectSet with defect_set_id={defect_set_id!r}"
                )
            return _row_to_defect_set(row)
        finally:
            conn.close()

    async def validate_defect_set(self, defect_set_id: str) -> DefectSet:
        """SM09: DEFECT_SET_GENERATED -> DEFECT_SET_GUARDED (draft -> validated).

        Домен/human review подтверждает, что дефекты резолвятся к
        source_standard_ref и нет случайной лишней ошибки (R0206 FD42-006) —
        это отдельный явный шаг, а не автоматический побочный эффект генерации.
        """
        return await to_thread(self._set_guard_status_sync, defect_set_id, "draft", "validated")

    async def release_defect_set(self, defect_set_id: str) -> DefectSet:
        """SM09: DEFECT_SET_GUARDED -> exposure-ready (validated -> released).

        Только "released" разрешает record_diagnosis() — до этого набор с
        эталонным ключом ответа не показывается студенту (T006-04).
        """
        return await to_thread(self._set_guard_status_sync, defect_set_id, "validated", "released")

    def _set_guard_status_sync(
        self, defect_set_id: str, required_from: str, to_status: str
    ) -> DefectSet:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM defect_sets WHERE defect_set_id = ?", (defect_set_id,)
            ).fetchone()
            if row is None:
                raise UnknownDefectSetError(
                    f"no DefectSet with defect_set_id={defect_set_id!r}"
                )
            if row["guard_status"] != required_from:
                raise GuardNotReleasedError(
                    f"defect_set_id={defect_set_id!r} has guard_status="
                    f"{row['guard_status']!r}, expected {required_from!r} "
                    f"before transitioning to {to_status!r}"
                )
            conn.execute(
                "UPDATE defect_sets SET guard_status = ? WHERE defect_set_id = ?",
                (to_status, defect_set_id),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM defect_sets WHERE defect_set_id = ?", (defect_set_id,)
            ).fetchone()
            return _row_to_defect_set(row)
        finally:
            conn.close()

    async def record_diagnosis(
        self,
        defect_set_ref: str,
        actor_id: str,
        found_items: list[str],
        correction_prompt_ref: str | None = None,
    ) -> StudentDiagnosis:
        return await to_thread(
            self._record_diagnosis_sync,
            defect_set_ref,
            actor_id,
            found_items,
            correction_prompt_ref,
        )

    def _record_diagnosis_sync(
        self,
        defect_set_ref: str,
        actor_id: str,
        found_items: list[str],
        correction_prompt_ref: str | None,
    ) -> StudentDiagnosis:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT guard_status FROM defect_sets WHERE defect_set_id = ?",
                (defect_set_ref,),
            ).fetchone()
            if row is None:
                raise UnknownDefectSetError(
                    f"no DefectSet with defect_set_id={defect_set_ref!r}"
                )
            # T006-04/T006-05 — diagnosis обязан ссылаться на released set,
            # а не на draft/validated с ещё не подтверждённым ключом ответа.
            if row["guard_status"] != "released":
                raise GuardNotReleasedError(
                    f"defect_set_id={defect_set_ref!r} has guard_status="
                    f"{row['guard_status']!r}, expected 'released' before "
                    "a StudentDiagnosis can reference it"
                )

            diagnosis = StudentDiagnosis(
                diagnosis_id=str(uuid.uuid4()),
                defect_set_ref=defect_set_ref,
                actor_id=actor_id,
                found_items=found_items,
                correction_prompt_ref=correction_prompt_ref,
                created_at=datetime.now(timezone.utc),
            )
            conn.execute(
                """
                INSERT INTO student_diagnoses
                    (diagnosis_id, defect_set_ref, actor_id, found_items_json,
                     correction_prompt_ref, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    diagnosis.diagnosis_id,
                    diagnosis.defect_set_ref,
                    diagnosis.actor_id,
                    json.dumps(diagnosis.found_items),
                    diagnosis.correction_prompt_ref,
                    diagnosis.created_at.isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return diagnosis

    async def get_diagnoses(self, defect_set_ref: str) -> list[StudentDiagnosis]:
        return await to_thread(self._get_diagnoses_sync, defect_set_ref)

    def _get_diagnoses_sync(self, defect_set_ref: str) -> list[StudentDiagnosis]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM student_diagnoses WHERE defect_set_ref = ?",
                (defect_set_ref,),
            ).fetchall()
            return [_row_to_diagnosis(row) for row in rows]
        finally:
            conn.close()

    async def check_diagnosis(self, diagnosis_id: str) -> DiagnosisScore:
        return await to_thread(self._check_diagnosis_sync, diagnosis_id)

    def _check_diagnosis_sync(self, diagnosis_id: str) -> DiagnosisScore:
        conn = self._connect()
        try:
            diagnosis_row = conn.execute(
                "SELECT * FROM student_diagnoses WHERE diagnosis_id = ?", (diagnosis_id,)
            ).fetchone()
            if diagnosis_row is None:
                raise UnknownDiagnosisError(f"no StudentDiagnosis with diagnosis_id={diagnosis_id!r}")

            defect_set_row = conn.execute(
                "SELECT defects_json FROM defect_sets WHERE defect_set_id = ?",
                (diagnosis_row["defect_set_ref"],),
            ).fetchone()
            if defect_set_row is None:
                raise UnknownDefectSetError(
                    f"no DefectSet with defect_set_id={diagnosis_row['defect_set_ref']!r}"
                )

            found_items = json.loads(diagnosis_row["found_items_json"])
            all_defect_ids = {d["defect_id"] for d in json.loads(defect_set_row["defects_json"])}
            found_set = set(found_items)

            return DiagnosisScore(
                diagnosis_id=diagnosis_id,
                correct_defect_ids=sorted(found_set & all_defect_ids),
                missed_defect_ids=sorted(all_defect_ids - found_set),
                false_positive_items=sorted(found_set - all_defect_ids),
            )
        finally:
            conn.close()
