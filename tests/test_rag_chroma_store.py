import csv

import pytest

from botkit.rag.chroma_store import ChromaVectorStore, EmbedderMismatchError
from botkit.rag.embeddings import EmbedderConfig

# These tests instantiate a real (small, CPU) HuggingFace embedder and a
# real on-disk Chroma collection — no mocks. They are slower than the pure
# ingest.py/quality.py/splitting.py tests but verify what actually matters
# here: that ingest+search work end to end and that the embedder-mismatch
# guard actually fires against real Chroma collection metadata.

EMBEDDER = EmbedderConfig(
    name="minilm-test",
    kind="huggingface",
    model="sentence-transformers/all-MiniLM-L6-v2",
    device="cpu",
)


def _make_csv(tmp_path):
    csv_path = tmp_path / "docs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source", "category"])
        writer.writerow(["Bad faith means denying one's own freedom.", "beauvoir.pdf", "philosophy"])
        writer.writerow(["The state has a monopoly on legitimate violence.", "weber.pdf", "philosophy"])
    return str(csv_path)


@pytest.mark.asyncio
async def test_ingest_and_search_roundtrip(tmp_path):
    csv_path = _make_csv(tmp_path)
    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )

    report = await store.ingest(csv_path)
    assert report.chunks_added == 2
    assert report.errors == []

    results = await store.search("who has a monopoly on force", k=1)
    assert len(results) == 1
    assert "monopoly" in results[0].text.lower()
    assert results[0].source == "weber.pdf"
    assert "ingested_at" in results[0].metadata


@pytest.mark.asyncio
async def test_reingest_is_idempotent(tmp_path):
    csv_path = _make_csv(tmp_path)
    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )

    await store.ingest(csv_path)
    await store.ingest(csv_path)

    all_docs = store._store.get(include=["metadatas"])
    assert len(all_docs["ids"]) == 2  # not duplicated


@pytest.mark.asyncio
async def test_embedder_mismatch_is_rejected(tmp_path):
    csv_path = _make_csv(tmp_path)
    persist_path = str(tmp_path / "db")

    store = ChromaVectorStore(embedder_config=EMBEDDER, persist_path=persist_path, collection="test_collection")
    await store.ingest(csv_path)

    other_embedder = EmbedderConfig(
        name="a-different-embedder",
        kind="huggingface",
        model="sentence-transformers/all-MiniLM-L6-v2",
    )
    with pytest.raises(EmbedderMismatchError):
        ChromaVectorStore(embedder_config=other_embedder, persist_path=persist_path, collection="test_collection")


@pytest.mark.asyncio
async def test_collection_survives_reopen(tmp_path):
    csv_path = _make_csv(tmp_path)
    persist_path = str(tmp_path / "db")

    store1 = ChromaVectorStore(embedder_config=EMBEDDER, persist_path=persist_path, collection="test_collection")
    await store1.ingest(csv_path)
    del store1

    store2 = ChromaVectorStore(embedder_config=EMBEDDER, persist_path=persist_path, collection="test_collection")
    results = await store2.search("bad faith and freedom", k=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_ingest_rejects_mismatched_collection_argument(tmp_path):
    csv_path = _make_csv(tmp_path)
    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )
    with pytest.raises(ValueError, match="bound to collection"):
        await store.ingest(csv_path, collection="some_other_collection")


@pytest.mark.asyncio
async def test_ingest_unsupported_extension_reports_error(tmp_path):
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("plain text file")

    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )
    report = await store.ingest(str(bad_file))
    assert report.chunks_added == 0
    assert any("unsupported_extension" in e for e in report.errors)


@pytest.mark.asyncio
async def test_ingest_shows_tqdm_progress_by_default(tmp_path, capsys):
    # tqdm is a dev/test-env dependency here — its presence is what makes
    # this test meaningful: on_progress not passed at all should still
    # produce visible progress output, not silence.
    csv_path = _make_csv(tmp_path)
    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )

    await store.ingest(csv_path)  # on_progress not passed at all

    captured = capsys.readouterr()
    assert "docs.csv" in captured.err


@pytest.mark.asyncio
async def test_ingest_on_progress_false_disables_default_tqdm(tmp_path, capsys):
    csv_path = _make_csv(tmp_path)
    store = ChromaVectorStore(
        embedder_config=EMBEDDER,
        persist_path=str(tmp_path / "db"),
        collection="test_collection",
    )

    await store.ingest(csv_path, on_progress=False)

    captured = capsys.readouterr()
    assert "docs.csv" not in captured.err
