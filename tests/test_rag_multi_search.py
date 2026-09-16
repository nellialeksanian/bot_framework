import csv

import pytest

from botkit.rag.chroma_store import ChromaVectorStore
from botkit.rag.embeddings import EmbedderConfig
from botkit.rag.multi_search import search_across

EMBEDDER = EmbedderConfig(
    name="minilm-test",
    kind="huggingface",
    model="sentence-transformers/all-MiniLM-L6-v2",
    device="cpu",
)


def _make_csv(tmp_path, filename, rows):
    csv_path = tmp_path / filename
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source"])
        for row in rows:
            writer.writerow(row)
    return str(csv_path)


async def _build_two_collections(tmp_path):
    persist_path = str(tmp_path / "db")

    philosophy_csv = _make_csv(
        tmp_path, "philosophy.csv",
        [["Bad faith means denying one's own freedom.", "beauvoir.pdf"]],
    )
    politics_csv = _make_csv(
        tmp_path, "politics.csv",
        [["The state has a monopoly on legitimate violence.", "weber.pdf"]],
    )

    philosophy_store = ChromaVectorStore(embedder_config=EMBEDDER, persist_path=persist_path, collection="philosophy")
    politics_store = ChromaVectorStore(embedder_config=EMBEDDER, persist_path=persist_path, collection="politics")

    await philosophy_store.ingest(philosophy_csv)
    await politics_store.ingest(politics_csv)

    return {"philosophy": philosophy_store, "politics": politics_store}


@pytest.mark.asyncio
async def test_search_across_respects_per_collection_k(tmp_path):
    stores = await _build_two_collections(tmp_path)

    results = await search_across(
        stores, "freedom and the state", collection_k={"philosophy": 1, "politics": 1}
    )

    assert set(results.keys()) == {"philosophy", "politics"}
    assert len(results["philosophy"]) == 1
    assert len(results["politics"]) == 1


@pytest.mark.asyncio
async def test_search_across_only_queries_selected_collections(tmp_path):
    stores = await _build_two_collections(tmp_path)

    results = await search_across(stores, "freedom", collection_k={"philosophy": 1})

    assert set(results.keys()) == {"philosophy"}


@pytest.mark.asyncio
async def test_search_across_tags_chunks_with_collection_metadata(tmp_path):
    stores = await _build_two_collections(tmp_path)

    results = await search_across(stores, "freedom", collection_k={"philosophy": 1})

    chunk = results["philosophy"][0]
    assert chunk.metadata["collection"] == "philosophy"


@pytest.mark.asyncio
async def test_search_across_rejects_unknown_collection(tmp_path):
    stores = await _build_two_collections(tmp_path)

    with pytest.raises(KeyError, match="nonexistent"):
        await search_across(stores, "freedom", collection_k={"nonexistent": 1})
