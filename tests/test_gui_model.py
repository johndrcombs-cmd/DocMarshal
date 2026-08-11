from dotdocs.gui import DotReviewApp
from dotdocs.gui_model import ReviewModel


def test_gui_formats_iso_dates_for_reviewers():
    assert DotReviewApp._display_date("2026-07-07") == "07/07/2026"
    assert DotReviewApp._display_date(None) == ""


def _results():
    return [
        {"source_file": "a.pdf", "status": "ready_for_review"},
        {"source_file": "b.pdf", "status": "needs_review"},
        {"source_file": "c.pdf", "status": "approved"},
        {"source_file": "d.pdf", "status": "failed"},
        {"source_file": "e.pdf", "status": "duplicate"},
        {"source_file": "f.pdf", "status": "not_dot"},
    ]


def test_counts_review_states():
    model = ReviewModel(_results())
    assert model.counts() == {
        "total": 6,
        "ready": 1,
        "needs_review": 1,
        "approved": 1,
        "failed": 1,
        "duplicate": 1,
        "not_dot": 1,
    }


def test_filters_review_states():
    model = ReviewModel(_results())
    assert [item["source_file"] for item in model.filtered("All")] == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
        "d.pdf",
        "e.pdf",
        "f.pdf",
    ]
    assert [item["source_file"] for item in model.filtered("Active")] == ["a.pdf", "b.pdf", "d.pdf"]
    assert [item["source_file"] for item in model.filtered("Needs Review")] == ["b.pdf"]
    assert [item["source_file"] for item in model.filtered("Ready")] == ["a.pdf"]
    assert [item["source_file"] for item in model.filtered("Approved")] == ["c.pdf"]
    assert [item["source_file"] for item in model.filtered("Duplicates")] == ["e.pdf"]
    assert [item["source_file"] for item in model.filtered("Not DOT")] == ["f.pdf"]


def test_replaces_result_by_source_file():
    model = ReviewModel(_results())
    replacement = {"source_file": "b.pdf", "status": "ready_for_review", "unit": "91"}
    model.replace(replacement)
    assert model.results[1] == replacement
    assert model.counts()["ready"] == 2
