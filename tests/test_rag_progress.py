import csv

from botkit.rag.ingest import ingest_csv
from botkit.rag.progress import tqdm_progress


def _make_csv(tmp_path, rows):
    csv_path = tmp_path / "docs.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["text", "source"])
        for row in rows:
            writer.writerow(row)
    return str(csv_path)


def test_tqdm_progress_runs_without_error(tmp_path, capsys):
    csv_path = _make_csv(
        tmp_path,
        [["First.", "doc1.pdf"], ["Second.", "doc2.pdf"], ["Third.", "doc3.pdf"]],
    )

    chunks, report = ingest_csv(csv_path, on_progress=tqdm_progress("docs.csv"))

    assert len(chunks) == 3
    # tqdm writes to stderr by default — just confirm something was printed,
    # not the exact bar formatting (which depends on terminal width/tqdm version).
    captured = capsys.readouterr()
    assert "docs.csv" in captured.err


def test_tqdm_progress_can_be_reused_across_separate_ingest_calls(tmp_path):
    # Each tqdm_progress(...) call must create an independent bar/state —
    # reusing the returned callback for a second, unrelated ingest should
    # not raise or carry over the "done" count from the first call.
    csv_path = _make_csv(tmp_path, [["Only row.", "doc1.pdf"]])

    on_progress = tqdm_progress("reused")
    ingest_csv(csv_path, on_progress=on_progress)
    ingest_csv(csv_path, on_progress=on_progress)
