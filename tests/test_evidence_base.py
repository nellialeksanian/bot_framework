from botkit.evidence.base import EvidenceLink, verify_and_record


class FakeChecker:
    def __init__(self, entailment: str):
        self._entailment = entailment
        self.calls: list[tuple[str, object]] = []

    async def verify(self, claim: str, chunk) -> EvidenceLink:
        self.calls.append((claim, chunk))
        return EvidenceLink(
            evidence_link_id="",
            claim=claim,
            chunk_ref=f"{chunk}::0",
            source=str(chunk),
            entailment=self._entailment,
            created_at="",
        )


class FakeStore:
    def __init__(self):
        self.recorded: list[EvidenceLink] = []

    async def record(self, link: EvidenceLink) -> EvidenceLink:
        stored = EvidenceLink(
            evidence_link_id="generated-id",
            claim=link.claim,
            chunk_ref=link.chunk_ref,
            source=link.source,
            entailment=link.entailment,
            created_at="2026-09-21T00:00:00+00:00",
        )
        self.recorded.append(stored)
        return stored

    async def get_for_claim(self, claim: str) -> list[EvidenceLink]:
        return [l for l in self.recorded if l.claim == claim]

    async def get_conflicting(self, claim: str) -> list[EvidenceLink]:
        return [l for l in self.recorded if l.claim == claim and l.entailment == "CONFLICTING"]


async def test_verify_and_record_calls_checker_then_store():
    checker = FakeChecker("SUPPORTED")
    store = FakeStore()

    result = await verify_and_record(checker, store, "the sky is blue", "chunk-object")

    assert checker.calls == [("the sky is blue", "chunk-object")]
    assert store.recorded == [result]
    assert result.evidence_link_id == "generated-id"
    assert result.entailment == "SUPPORTED"


async def test_verify_and_record_persists_result_retrievable_by_claim():
    checker = FakeChecker("CONFLICTING")
    store = FakeStore()

    await verify_and_record(checker, store, "claim-x", "chunk-object")

    links = await store.get_for_claim("claim-x")
    assert len(links) == 1
    assert links[0].entailment == "CONFLICTING"
