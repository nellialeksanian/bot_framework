import pytest

from botkit.attempts.base import attempt_count, record_revision
from botkit.attempts.sqlite_store import DuplicateResponseError, SQLiteAttemptStore


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "attempts.sqlite3")


@pytest.fixture
def store(db_path):
    return SQLiteAttemptStore(db_path)


async def test_record_creates_human_attempt_with_hash(store):
    attempt = await store.record("user-1", "task-1", "hello world", None)

    assert attempt.origin == "HUMAN"
    assert attempt.parent_attempt_id is None
    assert attempt.content_hash  # непустой хэш от content


async def test_record_never_overwrites_appends_new_revision(store):
    first = await store.record("user-1", "task-1", "draft v1", None)
    second = await store.record("user-1", "task-1", "draft v2", first.attempt_id)

    assert second.attempt_id != first.attempt_id
    assert second.parent_attempt_id == first.attempt_id
    # исходная попытка осталась неизменной
    lineage = await store.get_lineage(first.attempt_id)
    assert lineage == [first]


async def test_get_lineage_returns_root_first_chain(store):
    root = await store.record("user-1", "task-1", "v1", None)
    child = await store.record("user-1", "task-1", "v2", root.attempt_id)
    grandchild = await store.record("user-1", "task-1", "v3", child.attempt_id)

    lineage = await store.get_lineage(grandchild.attempt_id)

    assert [a.attempt_id for a in lineage] == [
        root.attempt_id,
        child.attempt_id,
        grandchild.attempt_id,
    ]


async def test_latest_returns_most_recent_attempt_for_actor_and_task(store):
    await store.record("user-1", "task-1", "v1", None)
    second = await store.record("user-1", "task-1", "v2", None)
    await store.record("user-2", "task-1", "other actor", None)

    latest = await store.latest("user-1", "task-1")

    assert latest.attempt_id == second.attempt_id


async def test_latest_returns_none_when_no_attempts(store):
    assert await store.latest("nobody", "task-1") is None


async def test_attempt_count_zero_when_no_attempts(store):
    assert await attempt_count(store, "user-1", "exam-1") == 0


async def test_attempt_count_tracks_revision_chain_length(store):
    await record_revision(store, "user-1", "exam-1", "attempt 1")
    assert await attempt_count(store, "user-1", "exam-1") == 1

    await record_revision(store, "user-1", "exam-1", "attempt 2")
    assert await attempt_count(store, "user-1", "exam-1") == 2

    await record_revision(store, "user-1", "exam-1", "attempt 3")
    assert await attempt_count(store, "user-1", "exam-1") == 3


async def test_attempt_count_is_independent_per_task_ref(store):
    await record_revision(store, "user-1", "exam-1", "attempt 1")
    await record_revision(store, "user-1", "exam-2", "attempt 1")
    await record_revision(store, "user-1", "exam-2", "attempt 2")

    assert await attempt_count(store, "user-1", "exam-1") == 1
    assert await attempt_count(store, "user-1", "exam-2") == 2


async def test_attempt_count_enforces_a_limit_before_recording(store):
    # Пример использования из docstring attempt_count(): проверка ДО
    # record_revision(), а не после — превышение лимита не создаёт (N+1)-ю
    # попытку молча.
    async def submit_with_limit(store, actor_id, task_ref, content, max_attempts):
        if await attempt_count(store, actor_id, task_ref) >= max_attempts:
            return None  # отказ — лимит исчерпан
        return await record_revision(store, actor_id, task_ref, content)

    for i in range(3):
        attempt = await submit_with_limit(store, "user-1", "exam-1", f"attempt {i+1}", max_attempts=3)
        assert attempt is not None

    fourth = await submit_with_limit(store, "user-1", "exam-1", "attempt 4", max_attempts=3)
    assert fourth is None
    assert await attempt_count(store, "user-1", "exam-1") == 3  # не выросло до 4


async def test_record_response_links_to_attempt(store):
    attempt = await store.record("user-1", "task-1", "my answer", None)

    response = await store.record_response(
        attempt.attempt_id,
        "bot reply",
        skill_context="ANALYTICAL_REVIEW",
        support_policy_ref="policy-v1",
        llm_response_ref="usage-event-1",
    )

    assert response.attempt_id == attempt.attempt_id
    fetched = await store.get_response(attempt.attempt_id)
    assert fetched == response


async def test_get_response_returns_none_when_absent(store):
    attempt = await store.record("user-1", "task-1", "my answer", None)
    assert await store.get_response(attempt.attempt_id) is None


async def test_record_response_is_strictly_one_to_one(store):
    attempt = await store.record("user-1", "task-1", "my answer", None)
    await store.record_response(attempt.attempt_id, "first reply")

    with pytest.raises(DuplicateResponseError):
        await store.record_response(attempt.attempt_id, "second reply")


async def test_record_revision_links_to_latest_as_parent(store):
    first = await record_revision(store, "user-1", "essay-1", "draft v1")
    second = await record_revision(store, "user-1", "essay-1", "draft v2")
    third = await record_revision(store, "user-1", "essay-1", "draft v3")

    assert first.parent_attempt_id is None
    assert second.parent_attempt_id == first.attempt_id
    assert third.parent_attempt_id == second.attempt_id

    lineage = await store.get_lineage(third.attempt_id)
    assert [a.attempt_id for a in lineage] == [
        first.attempt_id,
        second.attempt_id,
        third.attempt_id,
    ]


async def test_record_revision_keeps_separate_task_refs_independent(store):
    essay_v1 = await record_revision(store, "user-1", "essay-1", "essay draft")
    other_task = await record_revision(store, "user-1", "other-task", "unrelated work")

    assert other_task.parent_attempt_id is None  # разная тема — не цепляется к essay-1
    assert essay_v1.parent_attempt_id is None


async def test_data_survives_reopening_the_store(db_path):
    store = SQLiteAttemptStore(db_path)
    attempt = await store.record("user-1", "task-1", "persisted", None)
    await store.record_response(attempt.attempt_id, "persisted reply")

    reopened = SQLiteAttemptStore(db_path)

    assert await reopened.latest("user-1", "task-1") == attempt
    response = await reopened.get_response(attempt.attempt_id)
    assert response.content == "persisted reply"


async def test_active_session_defaults_to_none(store):
    assert await store.get_active_session("user-1", "feedback") is None


async def test_set_active_session_then_get_returns_it(store):
    await store.set_active_session("user-1", "feedback", "feedback::abc123")

    assert await store.get_active_session("user-1", "feedback") == "feedback::abc123"


async def test_set_active_session_overwrites_previous_value(store):
    await store.set_active_session("user-1", "feedback", "feedback::first")
    await store.set_active_session("user-1", "feedback", "feedback::second")

    assert await store.get_active_session("user-1", "feedback") == "feedback::second"


async def test_active_session_is_independent_per_skill(store):
    await store.set_active_session("user-1", "feedback", "feedback::ref")
    await store.set_active_session("user-1", "text_analysis", "text_analysis::ref")

    assert await store.get_active_session("user-1", "feedback") == "feedback::ref"
    assert await store.get_active_session("user-1", "text_analysis") == "text_analysis::ref"


async def test_active_session_is_independent_per_actor(store):
    await store.set_active_session("user-1", "feedback", "feedback::user1-ref")
    await store.set_active_session("user-2", "feedback", "feedback::user2-ref")

    assert await store.get_active_session("user-1", "feedback") == "feedback::user1-ref"
    assert await store.get_active_session("user-2", "feedback") == "feedback::user2-ref"


async def test_active_session_does_not_pollute_attempts_table(store):
    """Регрессионный тест для B2-рефакторинга: активная сессия хранится в
    собственной таблице, а не как синтетическая Attempt-запись — latest()
    по любому task_ref, связанному со служебным именем, не должен её видеть."""
    await store.set_active_session("user-1", "feedback", "feedback::real-ref")

    assert await store.latest("user-1", "feedback__active_session") is None


async def test_active_session_survives_reopening_the_store(db_path):
    store = SQLiteAttemptStore(db_path)
    await store.set_active_session("user-1", "feedback", "feedback::persisted-ref")

    reopened = SQLiteAttemptStore(db_path)

    assert await reopened.get_active_session("user-1", "feedback") == "feedback::persisted-ref"
