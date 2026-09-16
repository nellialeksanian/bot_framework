import csv
import os

import docx as docx_lib
import pytest

from botkit.rag.ingest import ingest_csv, ingest_docx, ingest_pdf
from botkit.rag.quality import looks_garbled, looks_like_scan
from botkit.rag.splitting import pack_sentences_into_chunks, sentence_aware_chunks


# --- CSV ingest ---

def test_ingest_csv_requires_source_column(tmp_path):
    csv_path = tmp_path / "no_source.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "category"])
        writer.writerow(["hello world", "cat1"])

    with pytest.raises(ValueError, match="source"):
        ingest_csv(str(csv_path))


def test_ingest_csv_requires_text_column(tmp_path):
    csv_path = tmp_path / "no_text.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source", "category"])
        writer.writerow(["doc1", "cat1"])

    with pytest.raises(ValueError, match="text"):
        ingest_csv(str(csv_path))


def test_ingest_csv_happy_path(tmp_path):
    csv_path = tmp_path / "docs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source", "category"])
        writer.writerow(["First document text.", "doc1.pdf", "philosophy"])
        writer.writerow(["Second document text.", "doc2.pdf", "law"])

    chunks, report = ingest_csv(str(csv_path))

    assert len(chunks) == 2
    assert report.chunks_added == 2
    assert report.errors == []

    assert chunks[0].text == "First document text."
    assert chunks[0].source == "doc1.pdf"
    assert chunks[0].metadata["category"] == "philosophy"
    assert "ingested_at" in chunks[0].metadata

    assert chunks[1].source == "doc2.pdf"


def test_ingest_csv_warns_on_empty_source(tmp_path):
    csv_path = tmp_path / "empty_source.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source"])
        writer.writerow(["Some text.", ""])

    chunks, report = ingest_csv(str(csv_path))

    assert len(chunks) == 1
    assert any("empty_source_row_0" in w for w in report.warnings)


def test_ingest_csv_skips_empty_text_rows(tmp_path):
    csv_path = tmp_path / "empty_text.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source"])
        writer.writerow(["", "doc1.pdf"])
        writer.writerow(["Real text.", "doc2.pdf"])

    chunks, report = ingest_csv(str(csv_path))

    assert len(chunks) == 1
    assert chunks[0].source == "doc2.pdf"
    assert any("empty_text_row_0" in w for w in report.warnings)


# --- quality checks ---

def test_looks_like_scan_detects_empty_page():
    assert looks_like_scan("") is True
    assert looks_like_scan("a") is True
    assert looks_like_scan("x" * 100) is False


def test_looks_garbled_detects_control_chars():
    garbled = "�" * 50 + "normal text"
    assert looks_garbled(garbled) is True


def test_looks_garbled_accepts_normal_text():
    normal = "Это обычный русский текст с пунктуацией, цифрами 123 и всем прочим."
    assert looks_garbled(normal) is False


# --- sentence-aware splitting ---

def test_pack_sentences_never_splits_a_sentence():
    sentences = ["Short one.", "A somewhat longer sentence here.", "Third."]
    chunks = pack_sentences_into_chunks(sentences, chunk_size=30)

    # every original sentence must appear whole in exactly one chunk
    joined = " ".join(chunks)
    for s in sentences:
        assert s in joined


def test_sentence_aware_chunks_ends_on_sentence_boundary():
    text = (
        "Первое предложение содержит некоторый текст. "
        "Второе предложение тоже здесь присутствует. "
        "Третье предложение завершает абзац."
    )
    chunks = sentence_aware_chunks(text, chunk_size=60, chunk_overlap=0, language="russian")

    assert len(chunks) >= 2
    for chunk in chunks:
        stripped = chunk.strip()
        assert stripped.endswith((".", "!", "?"))


def test_sentence_aware_chunks_overlap_repeats_context():
    text = "Одно. Два. Три. Четыре. Пять."
    chunks = sentence_aware_chunks(text, chunk_size=10, chunk_overlap=5, language="russian")
    assert len(chunks) >= 2


# --- PDF ingest (real extraction, synthetic quality cases) ---

def test_ingest_pdf_missing_file_reports_error():
    chunks, report = ingest_pdf("/nonexistent/path.pdf")
    assert chunks == []
    assert len(report.errors) == 1
    assert "pdf_read_failed" in report.errors[0]


# --- DOCX ingest (real python-docx file) ---

def test_ingest_docx_extracts_chapters_and_metadata(tmp_path):
    docx_path = tmp_path / "sample.docx"
    doc = docx_lib.Document()
    doc.add_heading("Chapter One", level=1)
    doc.add_paragraph(
        "This is the first sentence of chapter one. "
        "This is the second sentence, still chapter one."
    )
    doc.add_heading("Chapter Two", level=1)
    doc.add_paragraph("This is chapter two's only sentence for now.")
    doc.save(str(docx_path))

    chunks, report = ingest_docx(str(docx_path), chunk_size=1000, language="english")

    assert report.errors == []
    assert len(chunks) >= 2

    chapters = {c.metadata.get("chapter") for c in chunks}
    assert "Chapter One" in chapters
    assert "Chapter Two" in chapters

    for c in chunks:
        assert c.source == "sample.docx"
        assert c.page is None
        assert "ingested_at" in c.metadata


def test_ingest_docx_missing_file_reports_error():
    chunks, report = ingest_docx("/nonexistent/path.docx")
    assert chunks == []
    assert len(report.errors) == 1
    assert "docx_read_failed" in report.errors[0]


# --- on_progress callback ---

def test_ingest_csv_reports_progress(tmp_path):
    csv_path = tmp_path / "docs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source"])
        writer.writerow(["First.", "doc1.pdf"])
        writer.writerow(["Second.", "doc2.pdf"])
        writer.writerow(["Third.", "doc3.pdf"])

    calls: list[tuple[int, int]] = []
    ingest_csv(str(csv_path), on_progress=lambda done, total: calls.append((done, total)))

    assert calls == [(1, 3), (2, 3), (3, 3)]


def test_ingest_pdf_reports_progress_per_page(tmp_path):
    # A missing/unreadable PDF still exercises the error path without
    # on_progress ever firing — this asserts it's not called on failure.
    calls: list[tuple[int, int]] = []
    ingest_pdf("/nonexistent/path.pdf", on_progress=lambda done, total: calls.append((done, total)))
    assert calls == []


def test_ingest_docx_reports_progress_per_chapter(tmp_path):
    docx_path = tmp_path / "sample.docx"
    doc = docx_lib.Document()
    doc.add_heading("Chapter One", level=1)
    doc.add_paragraph("First sentence of chapter one.")
    doc.add_heading("Chapter Two", level=1)
    doc.add_paragraph("First sentence of chapter two.")
    doc.save(str(docx_path))

    calls: list[tuple[int, int]] = []
    ingest_docx(
        str(docx_path),
        chunk_size=1000,
        language="english",
        on_progress=lambda done, total: calls.append((done, total)),
    )

    assert calls == [(1, 2), (2, 2)]
