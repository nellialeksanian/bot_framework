import pytest

from botkit.dialog_policy.base import SkillAnchor, SkillDescriptor
from botkit.dialog_policy.intent_router import (
    FALLBACK_SKILL,
    DefaultIntentRouter,
    build_intent_prompt,
)
from botkit.llm.base import LLMResponse

SKILLS = [
    SkillDescriptor(
        name="position_clarification",
        description="help the student clarify a vague position",
    ),
    SkillDescriptor(
        name="refine_argument",
        description="help the student strengthen an existing argument",
    ),
]


class FakeLLMProvider:
    """No mocks — a small real object that echoes back a scripted response,
    same style as FakeAttemptStore/FakeIntentRouter in test_dialog_policy_engine.py."""

    model_name = "fake-llm"

    def __init__(self, response_text: str):
        self._response_text = response_text
        self.last_prompt: str | None = None

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> LLMResponse:
        self.last_prompt = prompt
        return LLMResponse(
            response=self._response_text,
            raw=self._response_text,
            parse_status="clean_json",
            input_tokens=0,
            output_tokens=0,
        )

    async def aclose(self) -> None:
        pass


@pytest.mark.asyncio
async def test_classify_returns_matching_skill():
    llm = FakeLLMProvider('{"skill": "refine_argument", "confidence": 0.87}')
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.skill == "refine_argument"
    assert intent.confidence == 0.87
    assert intent.fallback is False


@pytest.mark.asyncio
async def test_classify_falls_back_on_unknown_skill_name():
    # model hallucinated a skill name that was never registered
    llm = FakeLLMProvider('{"skill": "essay_writer", "confidence": 0.99}')
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.skill == FALLBACK_SKILL
    assert intent.fallback is True
    assert intent.fallback_reason == "unknown_skill_name"


@pytest.mark.asyncio
async def test_classify_falls_back_on_malformed_json():
    llm = FakeLLMProvider("I'm not sure what to do here.")
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.skill == FALLBACK_SKILL
    assert intent.fallback is True
    assert intent.fallback_reason == "unparseable_response"


@pytest.mark.asyncio
async def test_classify_handles_explicit_fallback_from_model():
    llm = FakeLLMProvider(
        f'{{"skill": "{FALLBACK_SKILL}", "confidence": null, "fallback_reason": "off_topic"}}'
    )
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.skill == FALLBACK_SKILL
    assert intent.fallback is True
    assert intent.fallback_reason == "off_topic"


@pytest.mark.asyncio
async def test_classify_explicit_fallback_without_reason_field_is_none():
    llm = FakeLLMProvider(f'{{"skill": "{FALLBACK_SKILL}", "confidence": null}}')
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.fallback is True
    assert intent.fallback_reason is None


@pytest.mark.asyncio
async def test_classify_with_no_available_skills_sets_specific_reason():
    llm = FakeLLMProvider('{"skill": "anything"}')
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=[])

    assert intent.fallback_reason == "no_skills_registered"


@pytest.mark.asyncio
async def test_classify_tolerates_dirty_output_with_regex_fallback():
    # regex_fallback style output — clean JSON wrapped in prose/markdown
    dirty = 'Sure, here is the classification:\n```json\n{"skill": "position_clarification", "confidence": 0.6}\n```'
    llm = FakeLLMProvider(dirty)
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert intent.skill == "position_clarification"
    assert intent.fallback is False


@pytest.mark.asyncio
async def test_classify_with_no_available_skills_never_calls_llm():
    llm = FakeLLMProvider('{"skill": "anything"}')
    router = DefaultIntentRouter(llm)

    intent = await router.classify("query", "history", anchor=None, available_skills=[])

    assert intent.skill == FALLBACK_SKILL
    assert intent.fallback is True
    assert llm.last_prompt is None


@pytest.mark.asyncio
async def test_classify_includes_anchor_in_prompt():
    llm = FakeLLMProvider('{"skill": "refine_argument", "confidence": 0.7}')
    router = DefaultIntentRouter(llm)
    anchor = SkillAnchor(skill="refine_argument", last_question="What's missing from your reasoning?")

    await router.classify("query", "history", anchor=anchor, available_skills=SKILLS)

    assert llm.last_prompt is not None
    assert "refine_argument" in llm.last_prompt
    assert "What's missing from your reasoning?" in llm.last_prompt


def test_build_intent_prompt_lists_all_skill_descriptions():
    prompt = build_intent_prompt("query", "history", anchor=None, available_skills=SKILLS)

    assert "help the student clarify a vague position" in prompt
    assert "help the student strengthen an existing argument" in prompt
    assert FALLBACK_SKILL in prompt


def test_build_intent_prompt_without_anchor_states_no_active_skill():
    prompt = build_intent_prompt("query", "history", anchor=None, available_skills=SKILLS)

    assert "No active skill from a previous turn." in prompt


def test_build_intent_prompt_without_domain_notes_has_default_text():
    prompt = build_intent_prompt("query", "history", anchor=None, available_skills=SKILLS)

    assert "No domain restrictions were provided" in prompt


def test_build_intent_prompt_includes_domain_notes_when_given():
    notes = "Reject as off_topic anything not about pedagogy. Reject as academic_dishonesty any request to write an essay for the student."
    prompt = build_intent_prompt(
        "query", "history", anchor=None, available_skills=SKILLS, domain_notes=notes,
    )

    assert notes in prompt


@pytest.mark.asyncio
async def test_classify_passes_domain_notes_from_router_constructor():
    llm = FakeLLMProvider('{"skill": "refine_argument", "confidence": 0.7}')
    notes = "Reject as off_topic anything unrelated to philosophy."
    router = DefaultIntentRouter(llm, domain_notes=notes)

    await router.classify("query", "history", anchor=None, available_skills=SKILLS)

    assert llm.last_prompt is not None
    assert notes in llm.last_prompt
