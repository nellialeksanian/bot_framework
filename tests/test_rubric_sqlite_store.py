import pytest

from botkit.rubric.base import Criterion
from botkit.rubric.sqlite_store import (
    NoActivePackageError,
    SQLiteRubricStore,
    UnknownPackageError,
)


@pytest.fixture
def store(tmp_path):
    return SQLiteRubricStore(str(tmp_path / "rubric.sqlite3"))


def _dims(*labels: str) -> list[Criterion]:
    return [Criterion(dimension_id=f"dim_{l}", label=l, anchor_examples=[f"example of {l}"]) for l in labels]


async def test_register_package_creates_draft_by_default(store):
    package = await store.register_package("essay_grading", _dims("thesis", "evidence"))

    assert package.owner_status == "draft"
    assert package.version == 1
    assert package.domain == "essay_grading"
    assert [d.label for d in package.dimensions] == ["thesis", "evidence"]


async def test_get_active_package_raises_when_none_activated(store):
    await store.register_package("essay_grading", _dims("thesis"))  # stays draft

    with pytest.raises(NoActivePackageError):
        await store.get_active_package("essay_grading")


async def test_register_package_with_activate_true_is_immediately_active(store):
    package = await store.register_package("essay_grading", _dims("thesis"), activate=True)

    assert package.owner_status == "active"
    active = await store.get_active_package("essay_grading")
    assert active.package_id == package.package_id


async def test_activate_package_supersedes_previous_active(store):
    v1 = await store.register_package("essay_grading", _dims("thesis"), activate=True)
    v2 = await store.register_package("essay_grading", _dims("thesis", "evidence"))

    activated_v2 = await store.activate_package(v2.package_id)

    assert activated_v2.owner_status == "active"
    active = await store.get_active_package("essay_grading")
    assert active.package_id == v2.package_id
    assert active.version == 2


async def test_versions_increment_per_domain_independently(store):
    a1 = await store.register_package("essay_grading", _dims("thesis"))
    b1 = await store.register_package("argument_analysis", _dims("claim"))
    a2 = await store.register_package("essay_grading", _dims("thesis", "evidence"))

    assert a1.version == 1
    assert a2.version == 2
    assert b1.version == 1  # independent counter for a different domain


async def test_activate_unknown_package_raises(store):
    with pytest.raises(UnknownPackageError):
        await store.activate_package("nonexistent-id")


async def test_record_rating_links_to_existing_package(store):
    package = await store.register_package("essay_grading", _dims("thesis"), activate=True)

    rating = await store.record_rating(
        target_attempt_id="attempt-1",
        package_ref=package.package_id,
        criterion_version=package.version,
        rater_id="teacher-42",
        labels_found=["thesis"],
        evidence=["quote from the essay"],
        rater_qualification="teacher",
    )

    assert rating.target_attempt_id == "attempt-1"
    assert rating.package_ref == package.package_id
    assert rating.rater_qualification == "teacher"


async def test_record_rating_rejects_unknown_package(store):
    with pytest.raises(UnknownPackageError):
        await store.record_rating(
            target_attempt_id="attempt-1",
            package_ref="nonexistent-package",
            criterion_version=1,
            rater_id="teacher-42",
            labels_found=["thesis"],
            evidence=[],
        )


async def test_get_ratings_returns_all_ratings_for_attempt_append_only(store):
    package = await store.register_package("essay_grading", _dims("thesis"), activate=True)

    r1 = await store.record_rating(
        target_attempt_id="attempt-1", package_ref=package.package_id,
        criterion_version=package.version, rater_id="teacher-42",
        labels_found=["thesis"], evidence=["quote 1"],
    )
    r2 = await store.record_rating(
        target_attempt_id="attempt-1", package_ref=package.package_id,
        criterion_version=package.version, rater_id="teacher-99",
        labels_found=[], evidence=[],
    )

    ratings = await store.get_ratings("attempt-1")

    assert {r.rater_record_id for r in ratings} == {r1.rater_record_id, r2.rater_record_id}


async def test_get_ratings_returns_empty_list_when_none_recorded(store):
    assert await store.get_ratings("attempt-without-ratings") == []


async def test_data_survives_reopening_the_store(tmp_path):
    db_path = str(tmp_path / "rubric.sqlite3")
    store1 = SQLiteRubricStore(db_path)
    package = await store1.register_package("essay_grading", _dims("thesis"), activate=True)
    await store1.record_rating(
        target_attempt_id="attempt-1", package_ref=package.package_id,
        criterion_version=package.version, rater_id="teacher-42",
        labels_found=["thesis"], evidence=["quote"],
    )

    store2 = SQLiteRubricStore(db_path)
    reopened_active = await store2.get_active_package("essay_grading")
    reopened_ratings = await store2.get_ratings("attempt-1")

    assert reopened_active.package_id == package.package_id
    assert len(reopened_ratings) == 1
