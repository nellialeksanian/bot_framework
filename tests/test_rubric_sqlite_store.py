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


async def test_record_rating_without_scores_defaults_to_empty_dict(store):
    package = await store.register_package("essay_grading", _dims("thesis"), activate=True)

    rating = await store.record_rating(
        target_attempt_id="attempt-1", package_ref=package.package_id,
        criterion_version=package.version, rater_id="teacher-42",
        labels_found=["dim_thesis"], evidence=["quote"],
    )

    assert rating.scores == {}


async def test_record_rating_stores_and_returns_scores(store):
    package = await store.register_package("essay_grading", _dims("thesis"), activate=True)

    rating = await store.record_rating(
        target_attempt_id="attempt-1", package_ref=package.package_id,
        criterion_version=package.version, rater_id="teacher-42",
        labels_found=["dim_thesis"], evidence=["quote"],
        scores={"dim_thesis": 1.0},
    )
    fetched = await store.get_ratings("attempt-1")

    assert rating.scores == {"dim_thesis": 1.0}
    assert fetched[0].scores == {"dim_thesis": 1.0}


async def test_score_scale_and_scoring_rule_survive_reopening_the_store(tmp_path):
    db_path = str(tmp_path / "rubric.sqlite3")
    criterion = Criterion(
        dimension_id="logical_basis_unity", label="Единство логического основания",
        anchor_examples=["a"], score_scale=(0.0, 1.0, 2.0),
        scoring_rule="2 балла: ...; 1 балл: ...; 0 баллов: ...",
    )
    store1 = SQLiteRubricStore(db_path)
    await store1.register_package("putilin.versions", [criterion], activate=True)

    store2 = SQLiteRubricStore(db_path)
    reopened = await store2.get_active_package("putilin.versions")

    assert reopened.dimensions[0].score_scale == (0.0, 1.0, 2.0)
    assert reopened.dimensions[0].scoring_rule == "2 балла: ...; 1 балл: ...; 0 баллов: ..."


async def test_reopening_a_pre_scores_json_database_does_not_raise(tmp_path):
    """A database written before the score_scale/scoring_rule/scores
    extension has no scores_json column and no score_scale/scoring_rule keys
    in dimensions_json. SQLiteRubricStore must migrate the schema and read
    old rows with the new dataclass defaults, not raise or lose data."""
    import json
    import sqlite3

    db_path = str(tmp_path / "old.sqlite3")
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE criterion_packages (
            package_id TEXT PRIMARY KEY, domain TEXT NOT NULL, version INTEGER NOT NULL,
            dimensions_json TEXT NOT NULL, owner_status TEXT NOT NULL
        );
        CREATE TABLE rater_records (
            rater_record_id TEXT PRIMARY KEY, target_attempt_id TEXT NOT NULL,
            package_ref TEXT NOT NULL, criterion_version INTEGER NOT NULL,
            rater_id TEXT NOT NULL, rater_qualification TEXT,
            labels_found_json TEXT NOT NULL, evidence_json TEXT NOT NULL
        );
        """
    )
    old_dimensions = json.dumps([{"dimension_id": "x", "label": "X", "anchor_examples": []}])
    conn.execute(
        "INSERT INTO criterion_packages VALUES (?, ?, ?, ?, ?)",
        ("pkg-1", "essay_grading", 1, old_dimensions, "active"),
    )
    conn.execute(
        "INSERT INTO rater_records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("r1", "attempt-1", "pkg-1", 1, "teacher-42", None, "[]", "[]"),
    )
    conn.commit()
    conn.close()

    migrated = SQLiteRubricStore(db_path)
    package = await migrated.get_active_package("essay_grading")
    ratings = await migrated.get_ratings("attempt-1")

    assert package.dimensions[0].score_scale == (0.0, 1.0)
    assert package.dimensions[0].scoring_rule == ""
    assert ratings[0].scores == {}

    # And the migrated store is fully writable afterwards (ALTER TABLE
    # actually landed, not just tolerated on read).
    await migrated.record_rating(
        target_attempt_id="attempt-1", package_ref="pkg-1", criterion_version=1,
        rater_id="teacher-42", labels_found=[], evidence=[], scores={"x": 1.0},
    )
