import pytest

from botkit.attempts.base import record_revision
from botkit.attempts.sessions import (
    ActiveSessionResolver,
    classify_session_answer,
    DEFAULT_RU_SESSION_WORDS,
)
from botkit.attempts.sqlite_store import SQLiteAttemptStore


class FakeRevisionClassifier:
    """Test double for RevisionClassifier — returns a scripted decision
    instead of calling a real LLM."""

    def __init__(self, decision: bool | None):
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def is_revision(self, previous_text: str, new_text: str) -> bool | None:
        self.calls.append((previous_text, new_text))
        return self.decision


@pytest.fixture
def store(tmp_path):
    return SQLiteAttemptStore(str(tmp_path / "attempts.sqlite3"))


@pytest.fixture
def resolver(store):
    return ActiveSessionResolver(store)


def test_classify_session_answer_recognizes_continue_and_new():
    assert classify_session_answer("да", DEFAULT_RU_SESSION_WORDS) is True
    assert classify_session_answer("Продолжаем", DEFAULT_RU_SESSION_WORDS) is True
    assert classify_session_answer("новый", DEFAULT_RU_SESSION_WORDS) is False
    assert classify_session_answer("blah blah", DEFAULT_RU_SESSION_WORDS) is None


async def test_first_message_ever_resolves_without_asking(resolver):
    result = await resolver.resolve("user-1", "feedback", "первый черновик")

    assert result.task_ref is not None
    assert result.question_kind is None
    assert not resolver.is_awaiting_choice("user-1", "feedback")


async def test_without_classifier_second_message_asks_continue_or_new(resolver):
    """No classifier configured -> no automatic decision, always fall back
    to asking (the original, coarse-but-correct behavior)."""
    first = (await resolver.resolve("user-1", "feedback", "первый черновик")).task_ref

    result = await resolver.resolve("user-1", "feedback", "второе сообщение")

    assert result.task_ref is None
    assert result.question_kind == "ask_continue_or_new"
    assert resolver.is_awaiting_choice("user-1", "feedback")


async def test_without_classifier_answering_continue_resumes_same_task_ref(resolver):
    first = (await resolver.resolve("user-1", "feedback", "первый черновик")).task_ref
    await resolver.resolve("user-1", "feedback", "хочу продолжить")

    resumed = await resolver.resolve("user-1", "feedback", "да")

    assert resumed.task_ref == first
    assert resumed.question_kind is None


async def test_without_classifier_answering_new_starts_different_task_ref(resolver):
    first = (await resolver.resolve("user-1", "feedback", "первый черновик")).task_ref
    await resolver.resolve("user-1", "feedback", "другая работа")

    fresh = await resolver.resolve("user-1", "feedback", "новый")

    assert fresh.task_ref != first
    assert fresh.question_kind is None


async def test_without_classifier_ambiguous_answer_reprompts(resolver):
    await resolver.resolve("user-1", "feedback", "первый черновик")
    await resolver.resolve("user-1", "feedback", "ещё одно")  # triggers first question

    result = await resolver.resolve("user-1", "feedback", "чего-то не понимаю")

    assert result.task_ref is None
    assert result.question_kind == "reprompt_ambiguous"
    assert resolver.is_awaiting_choice("user-1", "feedback")


async def test_classifier_true_continues_automatically_without_asking(store):
    # Реальный порядок вызовов в навыке: resolve() -> record_revision() ->
    # resolve() снова для следующего сообщения — только тогда у
    # classifier.is_revision() есть с чем сравнивать (latest() в task_ref).
    classifier = FakeRevisionClassifier(decision=True)
    resolver = ActiveSessionResolver(store, classifier=classifier)

    first = (await resolver.resolve("user-1", "feedback", "v1 черновика")).task_ref
    await record_revision(store, "user-1", first, "v1 черновика")

    second = await resolver.resolve("user-1", "feedback", "v2 черновика, доработанная")

    assert second.task_ref == first
    assert second.question_kind is None
    assert not resolver.is_awaiting_choice("user-1", "feedback")
    assert classifier.calls == [("v1 черновика", "v2 черновика, доработанная")]


async def test_classifier_false_falls_back_to_asking(store):
    classifier = FakeRevisionClassifier(decision=False)
    resolver = ActiveSessionResolver(store, classifier=classifier)

    first = (await resolver.resolve("user-1", "feedback", "черновик про Ушинского")).task_ref
    await record_revision(store, "user-1", first, "черновик про Ушинского")

    result = await resolver.resolve("user-1", "feedback", "черновик про Макаренко")

    assert result.task_ref is None
    assert result.question_kind == "ask_continue_or_new"
    assert resolver.is_awaiting_choice("user-1", "feedback")

    fresh = await resolver.resolve("user-1", "feedback", "новый")
    assert fresh.task_ref != first


async def test_classifier_uncertain_falls_back_to_asking(store):
    classifier = FakeRevisionClassifier(decision=None)  # LLM not confident
    resolver = ActiveSessionResolver(store, classifier=classifier)

    first = (await resolver.resolve("user-1", "feedback", "текст 1")).task_ref
    await record_revision(store, "user-1", first, "текст 1")

    result = await resolver.resolve("user-1", "feedback", "текст 2")

    assert result.task_ref is None
    assert result.question_kind == "ask_continue_or_new"
    assert resolver.is_awaiting_choice("user-1", "feedback")


async def test_different_skills_have_independent_sessions(resolver):
    feedback_result = await resolver.resolve("user-1", "feedback", "черновик")
    text_analysis_result = await resolver.resolve("user-1", "text_analysis", "вопрос по тексту")

    assert feedback_result.task_ref != text_analysis_result.task_ref
    assert not resolver.is_awaiting_choice("user-1", "text_analysis")


async def test_session_choice_survives_resolver_recreation(store):
    resolver1 = ActiveSessionResolver(store)
    first = (await resolver1.resolve("user-1", "feedback", "первый черновик")).task_ref

    # simulate process restart: fresh resolver, same persistent store
    resolver2 = ActiveSessionResolver(store)
    await resolver2.resolve("user-1", "feedback", "новое сообщение после рестарта")
    resumed = await resolver2.resolve("user-1", "feedback", "да")

    assert resumed.task_ref == first


async def test_full_revision_chain_using_resolved_task_ref(store):
    classifier = FakeRevisionClassifier(decision=True)
    resolver = ActiveSessionResolver(store, classifier=classifier)

    task_ref_1 = (await resolver.resolve("user-1", "feedback", "v1")).task_ref
    attempt_v1 = await record_revision(store, "user-1", task_ref_1, "v1")

    task_ref_continue = (await resolver.resolve("user-1", "feedback", "v2 revision")).task_ref
    assert task_ref_continue == task_ref_1
    attempt_v2 = await record_revision(store, "user-1", task_ref_continue, "v2")

    assert attempt_v2.parent_attempt_id == attempt_v1.attempt_id

    classifier.decision = False  # now a genuinely unrelated new draft arrives
    result = await resolver.resolve("user-1", "feedback", "unrelated new work")
    assert result.task_ref is None
    task_ref_new = (await resolver.resolve("user-1", "feedback", "новый")).task_ref
    assert task_ref_new != task_ref_1
    attempt_new = await record_revision(store, "user-1", task_ref_new, "unrelated")

    assert attempt_new.parent_attempt_id is None  # separate lineage, not attached to v1/v2
