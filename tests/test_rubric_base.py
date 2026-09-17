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
