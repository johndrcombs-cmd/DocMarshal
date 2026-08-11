import hashlib

from dotdocs.evaluation import evaluate_review_results


def _review_result(path, *, status, unit=None, document_type=None, controlling_date=None):
    content = path.read_bytes()
    result = {
        "source_file": str(path),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "source_size": len(content),
        "status": status,
        "unit": unit,
        "document_type": document_type,
        "controlling_date": controlling_date,
        "page_suffix": None,
    }
    return result


def test_evaluation_is_keyed_by_fingerprint_and_separates_missing_from_wrong(tmp_path):
    first = tmp_path / "Incoming" / "first.pdf"
    second = tmp_path / "Processed" / "Duplicates" / "second.pdf"
    unrelated = tmp_path / "Exceptions" / "Not DOT" / "third.pdf"
    for path, content in ((first, b"first"), (second, b"second"), (unrelated, b"third")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    approved = _review_result(
        first,
        status="approved",
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
    )
    duplicate = _review_result(
        second,
        status="duplicate",
        unit="97",
        document_type="REG",
        controlling_date="2027-08-31",
    )
    duplicate["source_file"] = str(tmp_path / "Incoming" / "second.pdf")
    duplicate["duplicate_archived_file"] = str(second)
    not_dot = _review_result(unrelated, status="not_dot")
    not_dot["source_file"] = str(tmp_path / "Incoming" / "third.pdf")
    not_dot["not_dot_archived_file"] = str(unrelated)

    predictions = {
        first.name: {"unit": "91", "document_type": "RP", "controlling_date": "2026-07-07"},
        second.name: {"unit": None, "document_type": "REG", "controlling_date": "2027-07-31"},
        unrelated.name: {"unit": None, "document_type": "RP", "controlling_date": None},
    }

    report = evaluate_review_results(
        [approved, duplicate, not_dot],
        analyzer=lambda path: predictions[path.name],
    )

    assert report["document_count"] == 3
    assert report["status_counts"] == {"approved": 1, "duplicate": 1, "not_dot": 1}
    assert report["eligible_documents"] == 2
    assert report["all_fields_correct"] == 1
    assert report["field_quality"]["document_type"] == {
        "correct": 2,
        "missing": 0,
        "wrong_nonmissing": 0,
        "total": 2,
    }
    assert report["field_quality"]["unit"] == {
        "correct": 1,
        "missing": 1,
        "wrong_nonmissing": 0,
        "total": 2,
    }
    assert report["field_quality"]["controlling_date"] == {
        "correct": 1,
        "missing": 0,
        "wrong_nonmissing": 1,
        "total": 2,
    }
    assert set(report["labels_by_sha256"]) == {
        approved["source_sha256"],
        duplicate["source_sha256"],
        not_dot["source_sha256"],
    }
    assert report["fingerprint_errors"] == []


def test_evaluation_rejects_changed_reviewed_pdf(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"reviewed")
    result = _review_result(
        source,
        status="approved",
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
    )
    source.write_bytes(b"changed")

    report = evaluate_review_results([result], analyzer=lambda _path: {})

    assert report["eligible_documents"] == 0
    assert report["fingerprint_errors"] == [
        {"source_sha256": result["source_sha256"], "reason": "FILE_FINGERPRINT_MISMATCH"}
    ]
