import pytest

from botkit.attempts.sqlite_store import SQLiteAttemptStore
from botkit.simulation.persona import LLMPersonaSimulator, LLMRoleBoundaryClassifier
from botkit.simulation.sqlite_store import SQLiteSimulationLog


class FakeLLMResponse:
    def __init__(self, text: str):
        self.response = text
        self.raw = text


class FakeLLMProvider:
    """Test double for LLMProvider — returns scripted responses in order,
    one per call, instead of calling a real model."""

    model_name = "fake-model"

    def __init__(self, *scripted_responses: str):
        self._responses = list(scripted_responses)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> FakeLLMResponse:
        self.prompts.append(prompt)
        return FakeLLMResponse(self._responses.pop(0))

    async def aclose(self) -> None:
        pass


class FakeRoleBoundaryClassifier:
    def __init__(self, decision: bool | None):
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def is_role_boundary_violation(self, persona_prompt: str, candidate_response: str) -> bool | None:
        self.calls.append((persona_prompt, candidate_response))
        return self.decision


@pytest.fixture
def attempt_store(tmp_path):
    return SQLiteAttemptStore(str(tmp_path / "attempts.sqlite3"))


@pytest.fixture
def sim_log(tmp_path, attempt_store):
    return SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"), attempt_store)


# --- LLMRoleBoundaryClassifier: prompt parsing ------------------------------


async def test_role_boundary_classifier_parses_true():
    llm = FakeLLMProvider('{"violation": true}')
    classifier = LLMRoleBoundaryClassifier(llm)

    result = await classifier.is_role_boundary_violation("be a literal user", "you should rewrite step 2")

    assert result is True


async def test_role_boundary_classifier_parses_false():
    llm = FakeLLMProvider('{"violation": false}')
    classifier = LLMRoleBoundaryClassifier(llm)

    result = await classifier.is_role_boundary_violation("be a literal user", "I could not find the button")

    assert result is False


async def test_role_boundary_classifier_parses_null():
    llm = FakeLLMProvider('{"violation": null}')
    classifier = LLMRoleBoundaryClassifier(llm)

    result = await classifier.is_role_boundary_violation("be a literal user", "ambiguous text")

    assert result is None


async def test_role_boundary_classifier_unparseable_output_returns_none():
    llm = FakeLLMProvider("garbage output without json")
    classifier = LLMRoleBoundaryClassifier(llm)

    result = await classifier.is_role_boundary_violation("be a literal user", "some text")

    assert result is None


async def test_role_boundary_classifier_tolerates_plain_string_response():
    """Терпимость к легаси-клиентам, чей .ainvoke() отдаёт str напрямую —
    тот же приём, что LLMRevisionClassifier в attempts/sessions.py."""

    class StringLLM:
        model_name = "legacy"

        async def ainvoke(self, prompt, *, timeout=300.0):
            return '{"violation": true}'

    classifier = LLMRoleBoundaryClassifier(StringLLM())

    result = await classifier.is_role_boundary_violation("role", "response")

    assert result is True


# --- LLMPersonaSimulator: respond() wiring ----------------------------------


async def test_respond_records_turn_automatically(sim_log, attempt_store):
    """R1001 FD42-020: respond() пишет SimulationTurn без участия навыка —
    иначе история диалога теряется для любого навыка, который не вызывает
    record_turn() вручную (как BotResponse в B2 пишется внутри
    record_response(), а не отдельным действием навыка)."""
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    llm = FakeLLMProvider("a literal in-role response")
    simulator = LLMPersonaSimulator(llm, sim_log, "be a literal user")

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    await simulator.respond(run.run_id, "click the button")

    turns = await simulator.get_turns(run.run_id)
    assert len(turns) == 1
    assert turns[0].actor_message == "click the button"
    assert turns[0].persona_response == "a literal in-role response"


async def test_respond_without_classifier_does_not_record_obstacle(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    llm = FakeLLMProvider("a literal in-role response")
    simulator = LLMPersonaSimulator(llm, sim_log, "be a literal user")

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    response = await simulator.respond(run.run_id, "click the button")

    assert response == "a literal in-role response"
    assert await sim_log.get_obstacles(run.run_id) == []


async def test_respond_records_obstacle_when_classifier_flags_violation(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    llm = FakeLLMProvider("you should rewrite step 2 like this...")
    classifier = FakeRoleBoundaryClassifier(decision=True)
    simulator = LLMPersonaSimulator(llm, sim_log, "be a literal user", role_classifier=classifier)

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    response = await simulator.respond(run.run_id, "click the button")

    assert response == "you should rewrite step 2 like this..."
    obstacles = await sim_log.get_obstacles(run.run_id)
    assert len(obstacles) == 1
    assert obstacles[0].advisory_content_present is True
    assert obstacles[0].evidence_ref == response


async def test_respond_does_not_record_obstacle_when_classifier_clears_it(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    llm = FakeLLMProvider("I could not find the button on this screen")
    classifier = FakeRoleBoundaryClassifier(decision=False)
    simulator = LLMPersonaSimulator(llm, sim_log, "be a literal user", role_classifier=classifier)

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    await simulator.respond(run.run_id, "click the button")

    assert await sim_log.get_obstacles(run.run_id) == []


async def test_respond_treats_uncertain_classification_as_fail_closed(sim_log, attempt_store):
    """None (не уверен) трактуется как потенциальное нарушение — лучше
    ложно отметить препятствие, чем пропустить молча."""
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    llm = FakeLLMProvider("ambiguous response")
    classifier = FakeRoleBoundaryClassifier(decision=None)
    simulator = LLMPersonaSimulator(llm, sim_log, "be a literal user", role_classifier=classifier)

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    await simulator.respond(run.run_id, "click the button")

    obstacles = await sim_log.get_obstacles(run.run_id)
    assert len(obstacles) == 1


async def test_start_and_complete_delegate_to_simulation_log(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    simulator = LLMPersonaSimulator(FakeLLMProvider(), sim_log, "role")

    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )
    completed = await simulator.complete(run.run_id)

    assert completed.status == "completed"


async def test_start_without_attempt_works_when_gate_disabled(tmp_path):
    """FD42-007-подобный навык: LLMPersonaSimulator тоже не требует
    attempt_ref, если require_attempt не запрошен."""
    log_without_attempts = SQLiteSimulationLog(str(tmp_path / "simulation.sqlite3"))
    simulator = LLMPersonaSimulator(FakeLLMProvider(), log_without_attempts, "role")

    run = await simulator.start("student-1", "negotiation-1", 1, 1)

    assert run.attempt_ref is None
    assert run.status == "started"


async def test_get_active_run_delegates_to_simulation_log(sim_log, attempt_store):
    attempt = await attempt_store.record("student-1", "task-1", "draft", None)
    simulator = LLMPersonaSimulator(FakeLLMProvider(), sim_log, "role")
    run = await simulator.start(
        "student-1", "task-1", 1, 1, attempt_ref=attempt.attempt_id, require_attempt=True
    )

    active = await simulator.get_active_run("student-1", "task-1")

    assert active is not None
    assert active.run_id == run.run_id
