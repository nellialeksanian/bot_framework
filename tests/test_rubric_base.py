from botkit.rubric.base import Criterion, CriterionPackage, render_criteria_block


def test_render_criteria_block_includes_domain_version_and_dimensions():
    package = CriterionPackage(
        package_id="pkg-1",
        domain="essay_grading",
        version=2,
        dimensions=[
            Criterion(dimension_id="logical_gaps", label="Логические разрывы", anchor_examples=["вывод без промежуточных шагов"]),
            Criterion(dimension_id="source_grounding", label="Опора на источники", anchor_examples=["цитата без страницы", "утверждение без ссылки"]),
        ],
        owner_status="active",
    )

    block = render_criteria_block(package)

    assert "essay_grading" in block
    assert "версия 2" in block
    assert "logical_gaps" in block
    assert "Логические разрывы" in block
    assert "вывод без промежуточных шагов" in block
    assert "source_grounding" in block
    assert "цитата без страницы" in block
    assert "утверждение без ссылки" in block


def test_render_criteria_block_handles_empty_dimensions():
    package = CriterionPackage(
        package_id="pkg-1", domain="x", version=1, dimensions=[], owner_status="active",
    )

    block = render_criteria_block(package)

    assert "x" in block
    assert "версия 1" in block


def test_criterion_defaults_to_binary_scale_and_no_scoring_rule():
    criterion = Criterion(dimension_id="thesis", label="Тезис", anchor_examples=["a"])

    assert criterion.score_scale == (0.0, 1.0)
    assert criterion.scoring_rule == ""


def test_render_criteria_block_includes_scoring_rule_and_scale_when_set():
    package = CriterionPackage(
        package_id="pkg-1", domain="putilin.versions", version=1,
        dimensions=[
            Criterion(
                dimension_id="logical_basis_unity", label="Единство логического основания",
                anchor_examples=["смешение субъекта и способа в одной версии"],
                score_scale=(0.0, 1.0, 2.0),
                scoring_rule="2 балла: >1 основания. 1 балл: одноверсионность. 0 баллов: смешение рядов.",
            ),
        ],
        owner_status="active",
    )

    block = render_criteria_block(package)

    assert "Шкала баллов: 0.0/1.0/2.0" in block
    assert "2 балла: >1 основания" in block


def test_render_criteria_block_omits_scoring_rule_line_when_not_set():
    package = CriterionPackage(
        package_id="pkg-1", domain="x", version=1,
        dimensions=[Criterion(dimension_id="thesis", label="Тезис", anchor_examples=["a"])],
        owner_status="active",
    )

    block = render_criteria_block(package)

    assert "Шкала баллов" not in block


def test_dimensions_equal_treats_scoring_rule_change_as_a_real_change():
    # sync_package() (tested separately in test_rubric_sync_package.py) relies
    # on _dimensions_equal(); this test pins the underlying comparison logic
    # so a scoring_rule-only edit is not silently treated as a no-op.
    from botkit.rubric.base import _dimensions_equal

    a = [Criterion(dimension_id="x", label="X", anchor_examples=[], scoring_rule="old rule")]
    b = [Criterion(dimension_id="x", label="X", anchor_examples=[], scoring_rule="new rule")]

    assert _dimensions_equal(a, b) is False


def test_dimensions_equal_treats_score_scale_change_as_a_real_change():
    from botkit.rubric.base import _dimensions_equal

    a = [Criterion(dimension_id="x", label="X", anchor_examples=[], score_scale=(0.0, 1.0))]
    b = [Criterion(dimension_id="x", label="X", anchor_examples=[], score_scale=(0.0, 0.5, 1.0))]

    assert _dimensions_equal(a, b) is False
