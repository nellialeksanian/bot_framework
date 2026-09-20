import pytest

from botkit.simulation.base import SimulationRun
from botkit.simulation.routing import route_or_continue


class FakePersonaSimulator:
    """Test double for PersonaSimulator — scripted run/response, no real LLM."""

    def __init__(self, active_run: SimulationRun | None = None, response: str = "persona response"):
        self._active_run = active_run
        self._response = response
        self.completed_run_ids: list[str] = []
        self.respond_calls: list[tuple[str, str]] = []

    async def get_active_run(self, actor_id: str, task_ref: str) -> SimulationRun | None:
        return self._active_run

    async def respond(self, run_id: str, student_message: str) -> str:
        self.respond_calls.append((run_id, student_message))
        return self._response

    async def complete(self, run_id: str) -> SimulationRun:
        self.completed_run_ids.append(run_id)
        assert self._active_run is not None
        self._active_run.status = "completed"
        return self._active_run

    async def start(self, *args, **kwargs):
        raise NotImplementedError("not used by route_or_continue")


def _run(run_id: str = "run-1", status: str = "started") -> SimulationRun:
    return SimulationRun(
        run_id=run_id, actor_id="student-1", attempt_ref=None, task_ref="oral_defense::student-1",
        simulator_policy_version=1, literalness_profile_version=1, persona_profile_version=None,
        status=status, created_at=__import__("datetime").datetime.now(),
    )


async def test_not_active_when_no_run_exists():
    simulator = FakePersonaSimulator(active_run=None)

    result = await route_or_continue(simulator, "student-1", "oral_defense::student-1", "hello")

    assert result.outcome == "not_active"
    assert result.response is None
    assert result.run_id is None


async def test_continued_calls_respond_on_active_run():
    run = _run()
    simulator = FakePersonaSimulator(active_run=run, response="professor reacts")

    result = await route_or_continue(simulator, "student-1", "oral_defense::student-1", "my thesis is...")

    assert result.outcome == "continued"
    assert result.response == "professor reacts"
    assert result.run_id == "run-1"
    assert simulator.respond_calls == [("run-1", "my thesis is...")]


async def test_exited_on_exact_exit_phrase():
    run = _run()
    simulator = FakePersonaSimulator(active_run=run)

    result = await route_or_continue(simulator, "student-1", "oral_defense::student-1", "закончить")

    assert result.outcome == "exited"
    assert result.response is None
    assert result.run_id == "run-1"
    assert simulator.completed_run_ids == ["run-1"]
    assert simulator.respond_calls == []  # respond() must not be called on exit


async def test_exit_phrase_matching_is_case_and_whitespace_insensitive():
    run = _run()
    simulator = FakePersonaSimulator(active_run=run)

    result = await route_or_continue(simulator, "student-1", "oral_defense::student-1", "  ЗАКОНЧИТЬ  ")

    assert result.outcome == "exited"


async def test_custom_exit_phrases_override_default():
    run = _run()
    simulator = FakePersonaSimulator(active_run=run)

    result = await route_or_continue(
        simulator, "student-1", "oral_defense::student-1", "done",
        exit_phrases=frozenset({"done"}),
    )

    assert result.outcome == "exited"


async def test_default_exit_phrases_do_not_match_arbitrary_text():
    run = _run()
    simulator = FakePersonaSimulator(active_run=run)

    result = await route_or_continue(simulator, "student-1", "oral_defense::student-1", "I want to stop arguing")

    # "stop" as a substring of a longer sentence must NOT trigger exit —
    # only an exact match (after strip/lower) against exit_phrases.
    assert result.outcome == "continued"
