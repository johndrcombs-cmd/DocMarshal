from pathlib import Path

import fitz

from dotdocs.document_import import (
    find_tesseract,
    import_pdf_documents,
    ocr_candidate_paths,
    pdf_has_searchable_text,
    run_pdf_ocr,
)


def _pdf(path: Path, text: str = "") -> None:
    document = fitz.open()
    page = document.new_page(width=300, height=400)
    if text:
        page.insert_text((40, 60), text)
    document.save(path)
    document.close()


def test_manual_import_copies_only_pdfs_without_overwriting(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    first = tmp_path / "first.pdf"
    second = tmp_path / "second.PDF"
    ignored = tmp_path / "notes.txt"
    _pdf(first, "first")
    _pdf(second, "second")
    ignored.write_text("not a PDF", encoding="utf-8")

    imported = import_pdf_documents((first, second, ignored), incoming)

    assert [item["status"] for item in imported] == ["copied_verified", "copied_verified", "unsupported"]
    assert (incoming / first.name).read_bytes() == first.read_bytes()
    assert (incoming / second.name).read_bytes() == second.read_bytes()

    identical = import_pdf_documents((first,), incoming)
    assert identical[0]["status"] == "already_identical"

    conflicting = tmp_path / "other" / "first.pdf"
    conflicting.parent.mkdir()
    _pdf(conflicting, "different")
    collision = import_pdf_documents((conflicting,), incoming)
    assert collision[0]["status"] == "destination_conflict"
    assert (incoming / first.name).read_bytes() == first.read_bytes()


def test_detects_searchable_and_image_only_pdfs(tmp_path):
    searchable = tmp_path / "searchable.pdf"
    scanned = tmp_path / "scanned.pdf"
    _pdf(searchable, "searchable text")
    _pdf(scanned)

    assert pdf_has_searchable_text(searchable)
    assert not pdf_has_searchable_text(scanned)


def test_ocr_preserves_original_and_atomically_publishes_searchable_pdf(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    _pdf(source)
    original = source.read_bytes()

    def fake_page_ocr(_image_path: Path, output_pdf: Path) -> None:
        _pdf(output_pdf, "recognized OCR text")

    result = run_pdf_ocr(
        source,
        incoming_root=incoming,
        backup_root=tmp_path / "Processed" / "OCR Originals",
        page_ocr=fake_page_ocr,
    )

    backup = Path(result["backup_path"])
    assert result["status"] == "ocr_completed"
    assert backup.read_bytes() == original
    assert source.read_bytes() != original
    assert pdf_has_searchable_text(source)
    with fitz.open(source) as document:
        assert document.page_count == 1


def test_finds_standard_per_user_tesseract_install(monkeypatch, tmp_path):
    executable = tmp_path / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"exe")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("dotdocs.document_import.shutil.which", lambda _name: None)

    assert find_tesseract() == executable


def test_bulk_ocr_candidates_are_unique_flagged_pdfs_directly_in_incoming(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    first = incoming / "first.pdf"
    second = incoming / "second.pdf"
    searchable = incoming / "searchable.pdf"
    outside = tmp_path / "outside.pdf"
    for path in (first, second, searchable, outside):
        _pdf(path)
    results = [
        {"source_file": str(first), "reasons": ["NO_SEARCHABLE_TEXT"]},
        {"source_file": str(first), "reasons": ["PDF_REQUIRES_OCR"]},
        {"source_file": str(second), "reasons": ["PDF_REQUIRES_OCR"]},
        {"source_file": str(searchable), "reasons": ["CONTROLLING_DATE_UNKNOWN"]},
        {"source_file": str(outside), "reasons": ["NO_SEARCHABLE_TEXT"]},
    ]

    assert ocr_candidate_paths(results, incoming) == [first, second]
