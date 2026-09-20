import pytest

from botkit.attempts.sqlite_store import SQLiteAttemptStore
from botkit.simulation.sqlite_store import (
    DefectCountMismatchError,
    GuardNotReleasedError,
    MissingFirstDraftError,
    SQLiteScenarioGenerator,
    SQLiteSimulationLog,
    UnknownDefectSetError,
    UnknownDiagnosisError,
    UnknownRunError,
)


@pytest.fixture
def attempt_store(tmp_path):
    return SQLiteAttemptStore(str(tmp_path / "attempts.sqlite3"))


@pytest.fixture
def sim_log(tmp_path, attempt_store):
    return SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"), attempt_store)


@pytest.fixture
def scenario_gen(tmp_path):
    return SQLiteScenarioGenerator(str(tmp_path / "scenario.sqlite3"))


# --- SC02: SimulationRun + ExecutionObstacle -------------------------------
#
# Два режима, оба реальные потребители SC02 из реестра:
#  - require_attempt=True  — FD42-020 "ИИ-Эксплуатант": буквальный тест
#    человеческого черновика, FIRST_DRAFT_GATE обязателен (T020-02).
#  - require_attempt=False (по умолчанию) — напр. FD42-007 "Песочница
#    консультанта": свободный диалог с персоной без предшествующего
#    черновика — гейт неприменим, а не пропущен по ошибке.


async def test_start_requires_existing_attempt_when_gate_enabled(sim_log):
    with pytest.raises(MissingFirstDraftError):
        await sim_log.start(
            "student-1",
            task_ref="task-1",
            simulator_policy_version=1,
            literalness_profile_version=1,
            attempt_ref="nonexistent-attempt",
            require_attempt=True,
        )


async def test_start_rejects_missing_attempt_ref_when_gate_enabled(sim_log):
    with pytest.raises(MissingFirstDraftError):
        await sim_log.start(
            "student-1",
            task_ref="task-1",
            simulator_policy_version=1,
            literalness_profile_version=1,
            require_attempt=True,
            # attempt_ref omitted entirely — require_attempt=True with no
            # attempt_ref at all must fail the same way as an unknown one.
        )


async def test_start_succeeds_when_attempt_exists_and_gate_enabled(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "operator manual draft", None)

    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
        persona_profile_version=3,
    )

    assert run.actor_id == "student-1"
    assert run.attempt_ref == attempt.attempt_id
    assert run.status == "started"
    assert run.persona_profile_version == 3


async def test_start_without_attempt_ref_succeeds_when_gate_disabled(tmp_path):
    """FD42-007-подобный навык: переговорный тренажёр с персоной, где нет
    предшествующего человеческого черновика вообще — это не ошибка, гейт
    просто не применим. SQLiteSimulationLog не требует attempt_store здесь."""
    sim_log_without_attempts = SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"))

    run = await sim_log_without_attempts.start(
        "student-1",
        task_ref="negotiation-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        persona_profile_version=1,
    )

    assert run.attempt_ref is None
    assert run.status == "started"


async def test_complete_transitions_status(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )

    completed = await sim_log.complete(run.run_id)

    assert completed.status == "completed"
    assert (await sim_log.get_run(run.run_id)).status == "completed"


async def test_complete_unknown_run_raises(sim_log):
    with pytest.raises(UnknownRunError):
        await sim_log.complete("nonexistent-run")


async def test_record_obstacle_requires_locator_fields(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )

    obstacle = await sim_log.record_obstacle(
        run_ref=run.run_id,
        step_ref="step-3",
        obstacle_type="UNEXECUTABLE_STEP",
        evidence_ref="quote: 'click the button' but no button exists",
    )

    assert obstacle.step_ref == "step-3"
    assert obstacle.evidence_ref.startswith("quote:")
    assert obstacle.advisory_content_present is False
    assert obstacle.status == "open"


async def test_record_obstacle_marks_role_boundary_violation(sim_log, attempt_store):
    """R0404 FD42-020: advisory_content_present=True — структурный маркер
    нарушения роли (симулятор дал совет вместо реакции персоны), а не
    стилистическая деталь; должен быть виден вызывающему коду explicitly."""
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )

    obstacle = await sim_log.record_obstacle(
        run_ref=run.run_id,
        step_ref="step-1",
        obstacle_type="OTHER",
        evidence_ref="model said 'you should rewrite step 2 like this...'",
        advisory_content_present=True,
    )

    assert obstacle.advisory_content_present is True


async def test_record_obstacle_rejects_unknown_run(sim_log):
    with pytest.raises(UnknownRunError):
        await sim_log.record_obstacle(
            run_ref="nonexistent-run",
            step_ref="step-1",
            obstacle_type="OTHER",
            evidence_ref="evidence",
        )


async def test_get_obstacles_returns_all_for_run_append_only(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )
    o1 = await sim_log.record_obstacle(
        run_ref=run.run_id, step_ref="step-1", obstacle_type="MISSING_INFORMATION",
        evidence_ref="evidence 1",
    )
    o2 = await sim_log.record_obstacle(
        run_ref=run.run_id, step_ref="step-2", obstacle_type="AMBIGUOUS_INSTRUCTION",
        evidence_ref="evidence 2",
    )

    obstacles = await sim_log.get_obstacles(run.run_id)

    assert {o.obstacle_id for o in obstacles} == {o1.obstacle_id, o2.obstacle_id}


async def test_simulation_data_survives_reopening_the_store(tmp_path, attempt_store):
    db_path = str(tmp_path / "simulation.sqlite3")
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)

    log1 = SQLiteSimulationLog(db_path, attempt_store)
    run = await log1.start(
        "student-1",
        task_ref="task-1", simulator_policy_version=1, literalness_profile_version=1,
        attempt_ref=attempt.attempt_id, require_attempt=True,
    )
    await log1.record_obstacle(
        run_ref=run.run_id, step_ref="step-1", obstacle_type="OTHER", evidence_ref="ev",
    )

    log2 = SQLiteSimulationLog(db_path, attempt_store)
    reopened_run = await log2.get_run(run.run_id)
    reopened_obstacles = await log2.get_obstacles(run.run_id)

    assert reopened_run.run_id == run.run_id
    assert len(reopened_obstacles) == 1


# --- SC02: get_active_run() — точка обхода B1 route(), пока роль активна ---


async def test_get_active_run_returns_none_when_no_run(sim_log):
    assert await sim_log.get_active_run("student-1", "task-1") is None


async def test_get_active_run_finds_started_run(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )

    active = await sim_log.get_active_run("student-1", "task-1")

    assert active is not None
    assert active.run_id == run.run_id


async def test_get_active_run_returns_none_after_complete(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1",
        task_ref="task-1",
        simulator_policy_version=1,
        literalness_profile_version=1,
        attempt_ref=attempt.attempt_id,
        require_attempt=True,
    )
    await sim_log.complete(run.run_id)

    assert await sim_log.get_active_run("student-1", "task-1") is None


async def test_get_active_run_scoped_by_actor(tmp_path):
    """Разные студенты с той же task_ref не видят чужой активный run —
    каждый actor_id изолирован (аналог per-actor scoping в B2)."""
    sim_log = SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"))
    await sim_log.start("student-1", task_ref="negotiation-1", simulator_policy_version=1, literalness_profile_version=1)

    assert await sim_log.get_active_run("student-2", "negotiation-1") is None


async def test_get_active_run_works_without_attempt_store(tmp_path):
    """FD42-007-подобный навык: get_active_run() не требует attempt_store —
    actor_id теперь прямое поле, не выводится через attempt_ref."""
    sim_log_without_attempts = SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"))
    run = await sim_log_without_attempts.start(
        "student-1", task_ref="negotiation-1", simulator_policy_version=1, literalness_profile_version=1,
    )

    active = await sim_log_without_attempts.get_active_run("student-1", "negotiation-1")

    assert active is not None
    assert active.run_id == run.run_id


# --- SC02: SimulationTurn — R1001 FD42-020 (история диалога восстановима) --


async def test_record_turn_rejects_unknown_run(sim_log):
    with pytest.raises(UnknownRunError):
        await sim_log.record_turn("nonexistent-run", "student says hi", "persona reacts")


async def test_record_turn_assigns_sequential_turn_index(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1", task_ref="task-1", simulator_policy_version=1, literalness_profile_version=1,
        attempt_ref=attempt.attempt_id, require_attempt=True,
    )

    t0 = await sim_log.record_turn(run.run_id, "first message", "first response")
    t1 = await sim_log.record_turn(run.run_id, "second message", "second response")

    assert t0.turn_index == 0
    assert t1.turn_index == 1


async def test_get_turns_returns_root_first_order(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1", task_ref="task-1", simulator_policy_version=1, literalness_profile_version=1,
        attempt_ref=attempt.attempt_id, require_attempt=True,
    )
    await sim_log.record_turn(run.run_id, "message 1", "response 1")
    await sim_log.record_turn(run.run_id, "message 2", "response 2")
    await sim_log.record_turn(run.run_id, "message 3", "response 3")

    turns = await sim_log.get_turns(run.run_id)

    assert [t.actor_message for t in turns] == ["message 1", "message 2", "message 3"]
    assert [t.turn_index for t in turns] == [0, 1, 2]


async def test_get_turns_returns_empty_list_for_run_with_no_turns(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    run = await sim_log.start(
        "student-1", task_ref="task-1", simulator_policy_version=1, literalness_profile_version=1,
        attempt_ref=attempt.attempt_id, require_attempt=True,
    )

    assert await sim_log.get_turns(run.run_id) == []


async def test_turns_survive_reopening_the_store(tmp_path, attempt_store):
    db_path = str(tmp_path / "simulation.sqlite3")
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)

    log1 = SQLiteSimulationLog(db_path, attempt_store)
    run = await log1.start(
        "student-1", task_ref="task-1", simulator_policy_version=1, literalness_profile_version=1,
        attempt_ref=attempt.attempt_id, require_attempt=True,
    )
    await log1.record_turn(run.run_id, "message 1", "response 1")

    log2 = SQLiteSimulationLog(db_path, attempt_store)
    reopened_turns = await log2.get_turns(run.run_id)

    assert len(reopened_turns) == 1
    assert reopened_turns[0].actor_message == "message 1"


# --- SC03: DefectSet + StudentDiagnosis ------------------------------------


async def test_generate_defect_set_rejects_count_mismatch(scenario_gen):
    with pytest.raises(DefectCountMismatchError):
        await scenario_gen.generate_defect_set(
            task_ref="fasciola-1",
            source_standard_ref="gost-standard-v1",
            intended_defect_count=4,
            model_config_version="gpt-4o-2026-08",
            generator_policy_version=1,
            stimulus_text="a text with intentional defects",
            defects=[{"defect_type": "TIMING", "locator": "para-2", "description": "x"}] * 3,
        )


async def test_generate_defect_set_starts_as_draft(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1",
        source_standard_ref="gost-standard-v1",
        intended_defect_count=2,
        model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[
            {"defect_type": "TIMING", "locator": "para-2", "description": "x"},
            {"defect_type": "HOST", "locator": "para-5", "description": "y"},
        ],
    )

    assert defect_set.defects[0]["defect_id"] == "defect-0"
    assert defect_set.defects[1]["defect_id"] == "defect-1"

    assert defect_set.guard_status == "draft"
    assert defect_set.intended_defect_count == 2
    assert len(defect_set.defects) == 2


async def test_record_diagnosis_rejects_orphan_defect_set(scenario_gen):
    with pytest.raises(UnknownDefectSetError):
        await scenario_gen.record_diagnosis(
            defect_set_ref="nonexistent-set", actor_id="student-1", found_items=["d1"],
        )


async def test_record_diagnosis_rejects_draft_set_not_yet_released(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )

    with pytest.raises(GuardNotReleasedError):
        await scenario_gen.record_diagnosis(
            defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=[],
        )


async def test_record_diagnosis_rejects_validated_but_not_released_set(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )
    await scenario_gen.validate_defect_set(defect_set.defect_set_id)

    with pytest.raises(GuardNotReleasedError):
        await scenario_gen.record_diagnosis(
            defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=[],
        )


async def test_record_diagnosis_succeeds_after_release(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )
    await scenario_gen.validate_defect_set(defect_set.defect_set_id)
    await scenario_gen.release_defect_set(defect_set.defect_set_id)

    diagnosis = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id,
        actor_id="student-1",
        found_items=["defect-0"],
        correction_prompt_ref="prompt-ref-1",
    )

    assert diagnosis.defect_set_ref == defect_set.defect_set_id
    assert diagnosis.correction_prompt_ref == "prompt-ref-1"


async def test_release_defect_set_rejects_skipping_validation(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )

    with pytest.raises(GuardNotReleasedError):
        await scenario_gen.release_defect_set(defect_set.defect_set_id)


async def test_get_diagnoses_returns_all_for_set_append_only(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )
    await scenario_gen.validate_defect_set(defect_set.defect_set_id)
    await scenario_gen.release_defect_set(defect_set.defect_set_id)

    d1 = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=["defect-0"],
    )
    d2 = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-2", found_items=[],
    )

    diagnoses = await scenario_gen.get_diagnoses(defect_set.defect_set_id)

    assert {d.diagnosis_id for d in diagnoses} == {d1.diagnosis_id, d2.diagnosis_id}


async def test_scenario_data_survives_reopening_the_store(tmp_path):
    db_path = str(tmp_path / "scenario.sqlite3")
    gen1 = SQLiteScenarioGenerator(db_path)
    defect_set = await gen1.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=1, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with intentional defects",
        defects=[{"defect_type": "TIMING", "locator": "para-1", "description": "x"}],
    )
    await gen1.validate_defect_set(defect_set.defect_set_id)
    await gen1.release_defect_set(defect_set.defect_set_id)
    await gen1.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=["defect-0"],
    )

    gen2 = SQLiteScenarioGenerator(db_path)
    reopened_set = await gen2.get_defect_set(defect_set.defect_set_id)
    reopened_diagnoses = await gen2.get_diagnoses(defect_set.defect_set_id)

    assert reopened_set.guard_status == "released"
    assert len(reopened_diagnoses) == 1


# --- SC03: check_diagnosis() — сверка found_items с реальными defect_id ----


async def _released_four_defect_set(scenario_gen):
    defect_set = await scenario_gen.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-standard-v1",
        intended_defect_count=4, model_config_version="gpt-4o-2026-08",
        generator_policy_version=1,
        stimulus_text="a text with four intentional defects",
        defects=[
            {"defect_type": "TIMING", "locator": "para-1", "description": "a"},
            {"defect_type": "HOST", "locator": "para-2", "description": "b"},
            {"defect_type": "INFECTION_ROUTE", "locator": "para-3", "description": "c"},
            {"defect_type": "LOCALIZATION", "locator": "para-4", "description": "d"},
        ],
    )
    await scenario_gen.validate_defect_set(defect_set.defect_set_id)
    return await scenario_gen.release_defect_set(defect_set.defect_set_id)


async def test_check_diagnosis_rejects_unknown_diagnosis(scenario_gen):
    with pytest.raises(UnknownDiagnosisError):
        await scenario_gen.check_diagnosis("nonexistent-diagnosis")


async def test_check_diagnosis_reports_all_correct(scenario_gen):
    defect_set = await _released_four_defect_set(scenario_gen)
    diagnosis = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1",
        found_items=["defect-0", "defect-1", "defect-2", "defect-3"],
    )

    score = await scenario_gen.check_diagnosis(diagnosis.diagnosis_id)

    assert score.correct_defect_ids == ["defect-0", "defect-1", "defect-2", "defect-3"]
    assert score.missed_defect_ids == []
    assert score.false_positive_items == []


async def test_check_diagnosis_reports_partial_and_missed(scenario_gen):
    defect_set = await _released_four_defect_set(scenario_gen)
    diagnosis = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1",
        found_items=["defect-0", "defect-2"],
    )

    score = await scenario_gen.check_diagnosis(diagnosis.diagnosis_id)

    assert score.correct_defect_ids == ["defect-0", "defect-2"]
    assert score.missed_defect_ids == ["defect-1", "defect-3"]
    assert score.false_positive_items == []


async def test_check_diagnosis_reports_false_positives(scenario_gen):
    """Студент назвал что-то, не соответствующее ни одному реальному
    defect_id (напр. неверная метка, опечатка, или указал на несуществующую
    ошибку) — не должно молча засчитаться как правильный ответ."""
    defect_set = await _released_four_defect_set(scenario_gen)
    diagnosis = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1",
        found_items=["defect-0", "defect-99", "not-a-real-id"],
    )

    score = await scenario_gen.check_diagnosis(diagnosis.diagnosis_id)

    assert score.correct_defect_ids == ["defect-0"]
    assert score.missed_defect_ids == ["defect-1", "defect-2", "defect-3"]
    assert score.false_positive_items == ["defect-99", "not-a-real-id"]


async def test_check_diagnosis_reports_all_missed_when_nothing_found(scenario_gen):
    defect_set = await _released_four_defect_set(scenario_gen)
    diagnosis = await scenario_gen.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=[],
    )

    score = await scenario_gen.check_diagnosis(diagnosis.diagnosis_id)

    assert score.correct_defect_ids == []
    assert score.missed_defect_ids == ["defect-0", "defect-1", "defect-2", "defect-3"]
    assert score.false_positive_items == []
