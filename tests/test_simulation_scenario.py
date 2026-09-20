import pytest

from botkit.simulation.scenario import DefectGenerationParseError, LLMScenarioGenerator
from botkit.simulation.sqlite_store import DefectCountMismatchError, SQLiteScenarioGenerator


class FakeLLMResponse:
    def __init__(self, text: str):
        self.response = text
        self.raw = text


class FakeLLMProvider:
    model_name = "fake-model"

    def __init__(self, *scripted_responses: str):
        self._responses = list(scripted_responses)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> FakeLLMResponse:
        self.prompts.append(prompt)
        return FakeLLMResponse(self._responses.pop(0))

    async def aclose(self) -> None:
        pass


@pytest.fixture
def store(tmp_path):
    return SQLiteScenarioGenerator(str(tmp_path / "scenario.sqlite3"))


VALID_TWO_DEFECT_JSON = """
Here is the generated defect set.

{
  "stimulus_text": "Fasciola hepatica infects sheep and goats. The intermediate host is a marine snail. Cercariae directly penetrate human skin.",
  "defects": [
    {"defect_type": "HOST", "locator": "marine snail", "description": "wrong intermediate host — should be freshwater snail"},
    {"defect_type": "INFECTION_ROUTE", "locator": "directly penetrate human skin", "description": "wrong route — infection is via ingestion of adolescariae"}
  ]
}
""".strip()


async def test_generate_defect_set_parses_llm_json_and_stores_it(store):
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    defect_set = await generator.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
    )

    assert defect_set.guard_status == "draft"
    assert defect_set.intended_defect_count == 2
    assert len(defect_set.defects) == 2
    assert defect_set.defects[0]["defect_type"] == "HOST"
    assert "marine snail" in defect_set.stimulus_text


async def test_generate_defect_set_raises_on_unparseable_output(store):
    llm = FakeLLMProvider("this is not json at all")
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    with pytest.raises(DefectGenerationParseError):
        await generator.generate_defect_set(
            task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
        )


async def test_generate_defect_set_raises_on_malformed_json_object(store):
    llm = FakeLLMProvider("{not valid json, oops}")
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    with pytest.raises(DefectGenerationParseError):
        await generator.generate_defect_set(
            task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
        )


async def test_generate_defect_set_raises_when_stimulus_text_missing(store):
    """R0301 FD42-006: без stimulus_text нечего показать студенту — модель,
    вернувшая только defects без самого текста, должна получить явную
    ошибку, а не тихо создать DefectSet с пустым/отсутствующим текстом."""
    llm = FakeLLMProvider('{"defects": [{"defect_type": "HOST", "locator": "x", "description": "y"}]}')
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    with pytest.raises(DefectGenerationParseError):
        await generator.generate_defect_set(
            task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=1
        )


async def test_generate_defect_set_raises_when_stimulus_text_is_empty_string(store):
    llm = FakeLLMProvider('{"stimulus_text": "   ", "defects": []}')
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    with pytest.raises(DefectGenerationParseError):
        await generator.generate_defect_set(
            task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=0
        )


async def test_generate_defect_set_propagates_count_mismatch_from_store(store):
    """T006-02: LLM вернула валидный JSON, но не с тем числом дефектов,
    что заявлено — LLMScenarioGenerator не подделывает count, ошибку
    бросает нижележащий SQLiteScenarioGenerator."""
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)  # returns 2, but we'll ask for 3
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    with pytest.raises(DefectCountMismatchError):
        await generator.generate_defect_set(
            task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=3
        )


async def test_generate_defect_set_uses_constructor_model_config_by_default(store):
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    defect_set = await generator.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
    )

    assert defect_set.model_config_version == "gpt-4o-2026-08"


async def test_generate_defect_set_allows_overriding_model_config_per_call(store):
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )

    defect_set = await generator.generate_defect_set(
        task_ref="fasciola-1",
        source_standard_ref="gost-v1",
        intended_defect_count=2,
        model_config_version="gpt-4o-2026-09-override",
    )

    assert defect_set.model_config_version == "gpt-4o-2026-09-override"


async def test_record_diagnosis_delegates_to_store(store):
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )
    defect_set = await generator.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
    )
    await store.validate_defect_set(defect_set.defect_set_id)
    await store.release_defect_set(defect_set.defect_set_id)

    diagnosis = await generator.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=["defect-0"]
    )

    assert diagnosis.defect_set_ref == defect_set.defect_set_id


async def test_check_diagnosis_delegates_to_store(store):
    llm = FakeLLMProvider(VALID_TWO_DEFECT_JSON)
    generator = LLMScenarioGenerator(
        llm, store, "veterinary helminthology standard", model_config_version="gpt-4o-2026-08"
    )
    defect_set = await generator.generate_defect_set(
        task_ref="fasciola-1", source_standard_ref="gost-v1", intended_defect_count=2
    )
    await store.validate_defect_set(defect_set.defect_set_id)
    await store.release_defect_set(defect_set.defect_set_id)
    diagnosis = await generator.record_diagnosis(
        defect_set_ref=defect_set.defect_set_id, actor_id="student-1", found_items=["defect-0"]
    )

    score = await generator.check_diagnosis(diagnosis.diagnosis_id)

    assert score.correct_defect_ids == ["defect-0"]
    assert score.missed_defect_ids == ["defect-1"]
