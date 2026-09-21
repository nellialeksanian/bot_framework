from botkit.evidence.base import EvidenceLink
from botkit.evidence.sqlite_store import SQLiteEvidenceStore


def _link(claim: str = "claim-1", entailment: str = "SUPPORTED", chunk_ref: str = "doc.pdf::0", source: str = "doc.pdf") -> EvidenceLink:
    return EvidenceLink(
        evidence_link_id="",
        claim=claim,
        chunk_ref=chunk_ref,
        source=source,
        entailment=entailment,
        created_at="",
    )


async def test_record_generates_id_and_timestamp_when_missing(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))

    stored = await store.record(_link())

    assert stored.evidence_link_id
    assert stored.created_at


async def test_record_is_append_only_multiple_links_for_same_claim(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))

    first = await store.record(_link(entailment="SUPPORTED"))
    second = await store.record(_link(entailment="CONFLICTING"))

    links = await store.get_for_claim("claim-1")

    assert {l.evidence_link_id for l in links} == {first.evidence_link_id, second.evidence_link_id}


async def test_get_for_claim_returns_empty_list_when_none_recorded(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))

    assert await store.get_for_claim("no-such-claim") == []


async def test_get_conflicting_filters_by_entailment(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))

    await store.record(_link(entailment="SUPPORTED", chunk_ref="a.pdf::0", source="a.pdf"))
    conflicting = await store.record(_link(entailment="CONFLICTING", chunk_ref="b.pdf::1", source="b.pdf"))
    await store.record(_link(entailment="UNKNOWN", chunk_ref="c.pdf::2", source="c.pdf"))

    results = await store.get_conflicting("claim-1")

    assert [l.evidence_link_id for l in results] == [conflicting.evidence_link_id]


async def test_get_conflicting_returns_empty_when_no_conflicts(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))
    await store.record(_link(entailment="SUPPORTED"))

    assert await store.get_conflicting("claim-1") == []


async def test_data_survives_reopening_the_store(tmp_path):
    db_path = str(tmp_path / "evidence.sqlite3")
    store1 = SQLiteEvidenceStore(db_path)
    stored = await store1.record(_link())

    store2 = SQLiteEvidenceStore(db_path)
    reopened = await store2.get_for_claim("claim-1")

    assert [l.evidence_link_id for l in reopened] == [stored.evidence_link_id]


async def test_different_claims_are_isolated(tmp_path):
    store = SQLiteEvidenceStore(str(tmp_path / "evidence.sqlite3"))

    await store.record(_link(claim="claim-a"))
    await store.record(_link(claim="claim-b"))

    assert len(await store.get_for_claim("claim-a")) == 1
    assert len(await store.get_for_claim("claim-b")) == 1
