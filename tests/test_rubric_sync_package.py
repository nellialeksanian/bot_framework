import pytest

from botkit.rubric.base import Criterion, sync_package
from botkit.rubric.sqlite_store import SQLiteRubricStore


@pytest.fixture
def store(tmp_path):
    return SQLiteRubricStore(str(tmp_path / "rubric.sqlite3"))


def _dims(*labels: str) -> list[Criterion]:
    return [Criterion(dimension_id=f"dim_{l}", label=l, anchor_examples=[f"example of {l}"]) for l in labels]


async def test_first_call_registers_and_activates(store):
    package = await sync_package(store, "essay_grading", _dims("thesis", "evidence"))

    assert package.owner_status == "active"
    assert package.version == 1
    active = await store.get_active_package("essay_grading")
    assert active.package_id == package.package_id


async def test_repeated_call_with_same_dimensions_is_a_no_op(store):
    first = await sync_package(store, "essay_grading", _dims("thesis", "evidence"))
    second = await sync_package(store, "essay_grading", _dims("thesis", "evidence"))

    assert second.package_id == first.package_id
    assert second.version == 1  # no new version created


async def test_changed_dimensions_creates_new_version_and_activates_it(store):
    v1 = await sync_package(store, "essay_grading", _dims("thesis", "evidence"))
    v2 = await sync_package(store, "essay_grading", _dims("thesis", "evidence", "clarity"))

    assert v2.package_id != v1.package_id
    assert v2.version == 2
    active = await store.get_active_package("essay_grading")
    assert active.package_id == v2.package_id


async def test_previous_version_is_superseded_not_deleted(store):
    v1 = await sync_package(store, "essay_grading", _dims("thesis"))
    await sync_package(store, "essay_grading", _dims("thesis", "evidence"))

    ratings_v1_still_valid = await store.record_rating(
        target_attempt_id="attempt-1", package_ref=v1.package_id,
        criterion_version=v1.version, rater_id="teacher-1",
        labels_found=["dim_thesis"], evidence=[],
    )
    assert ratings_v1_still_valid.package_ref == v1.package_id  # old package still referenceable


async def test_changed_label_on_same_dimension_id_triggers_new_version(store):
    v1 = await sync_package(store, "essay_grading", [
        Criterion(dimension_id="thesis", label="Old label", anchor_examples=["a"]),
    ])
    v2 = await sync_package(store, "essay_grading", [
        Criterion(dimension_id="thesis", label="New label", anchor_examples=["a"]),
    ])

    assert v2.package_id != v1.package_id
    assert v2.dimensions[0].label == "New label"


async def test_different_domains_are_independent(store):
    a = await sync_package(store, "essay_grading", _dims("thesis"))
    b = await sync_package(store, "argument_analysis", _dims("claim"))

    assert a.domain == "essay_grading"
    assert b.domain == "argument_analysis"
    assert a.version == 1
    assert b.version == 1
