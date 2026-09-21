from botkit.evidence.llm_checker import LLMEvidenceChecker
from botkit.rag.base import Chunk


class FakeLLMResponse:
    def __init__(self, text: str):
        self.response = text
        self.raw = text


class FakeLLMProvider:
    """Test double for LLMProvider — returns scripted responses in order,
    one per call, instead of calling a real model."""

    model_name = "fake-model"

    def __init__(self, *scripted_responses: str):
        self._responses = list(scripted_responses)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str, *, timeout: float = 300.0) -> FakeLLMResponse:
        self.prompts.append(prompt)
        return FakeLLMResponse(self._responses.pop(0))

    async def aclose(self) -> None:
        pass


def _chunk(text: str = "some source text", source: str = "doc.pdf", chunk_index: int = 0) -> Chunk:
    return Chunk(text=text, source=source, page=1, chunk_index=chunk_index, metadata={})


async def test_verify_parses_supported():
    llm = FakeLLMProvider('{"entailment": "SUPPORTED"}')
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("the sky is blue", _chunk("the sky appears blue due to Rayleigh scattering"))

    assert link.entailment == "SUPPORTED"
    assert link.claim == "the sky is blue"


async def test_verify_parses_partial():
    llm = FakeLLMProvider('{"entailment": "PARTIAL"}')
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("claim", _chunk())

    assert link.entailment == "PARTIAL"


async def test_verify_parses_conflicting():
    llm = FakeLLMProvider('{"entailment": "CONFLICTING"}')
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("claim", _chunk())

    assert link.entailment == "CONFLICTING"


async def test_verify_parses_unknown():
    llm = FakeLLMProvider('{"entailment": "UNKNOWN"}')
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("claim", _chunk())

    assert link.entailment == "UNKNOWN"


async def test_verify_unparseable_output_fails_closed_to_unknown():
    """Инвариант 5.7 / B5: неразборчивый ответ классификатора не должен
    угадывать в сторону SUPPORTED — только UNKNOWN, как is_revision()
    в B2 возвращает None (не False) при неуверенности."""
    llm = FakeLLMProvider("garbage output without json")
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("claim", _chunk())

    assert link.entailment == "UNKNOWN"


async def test_verify_tolerates_plain_string_response():
    class StringLLM:
        model_name = "legacy"

        async def ainvoke(self, prompt, *, timeout=300.0):
            return '{"entailment": "SUPPORTED"}'

    checker = LLMEvidenceChecker(StringLLM())

    link = await checker.verify("claim", _chunk())

    assert link.entailment == "SUPPORTED"


async def test_verify_derives_chunk_ref_and_source_from_chunk():
    llm = FakeLLMProvider('{"entailment": "SUPPORTED"}')
    checker = LLMEvidenceChecker(llm)

    link = await checker.verify("claim", _chunk(source="lecture_04.pdf", chunk_index=7))

    assert link.source == "lecture_04.pdf"
    assert link.chunk_ref == "lecture_04.pdf::7"


async def test_verify_includes_claim_text_in_prompt():
    llm = FakeLLMProvider('{"entailment": "SUPPORTED"}')
    checker = LLMEvidenceChecker(llm)

    await checker.verify("water boils at 100C at sea level", _chunk("boiling point of water is 100 degrees Celsius"))

    assert "water boils at 100C at sea level" in llm.prompts[0]
    assert "boiling point of water is 100 degrees Celsius" in llm.prompts[0]
