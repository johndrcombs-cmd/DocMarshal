import json
import hashlib
from pathlib import Path

import pytest
from openpyxl import Workbook

import dotdocs.assets as assets
import dotdocs.review as review_module
from dotdocs.database import import_fleet_workbook
from dotdocs.review import (
    ApprovalError,
    ReviewValidationError,
    apply_correction,
    approve_document,
    mark_duplicate_document,
    mark_not_dot_document,
    record_asset_created,
    restore_archived_document,
)


def _unit_root(tmp_path):
    root = tmp_path / "units"
    for folder in ("001_Annual_DOT", "002_Insurance", "003_Registration", "004_Maintenance_Records"):
        (root / "Unit_91" / folder).mkdir(parents=True, exist_ok=True)
    return root


def _result(tmp_path):
    source = tmp_path / "Incoming" / "scan.pdf"
    source.parent.mkdir()
    source.write_bytes(b"%PDF-verified-source")
    content = source.read_bytes()
    return {
        "source_file": str(source),
        "status": "needs_review",
        "reasons": ["CONTROLLING_DATE_UNKNOWN"],
        "unit": "91",
        "document_type": "RP",
        "controlling_date": None,
        "proposed_filename": None,
        "proposed_destination": None,
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "source_size": len(content),
    }


def _fleet_database(tmp_path, unit="122", owner=""):
    workbook_path = tmp_path / "fleet.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Vin", "Asset Owner"])
    sheet.append([unit, "4ZEDT1628T3366133", owner])
    workbook.save(workbook_path)
    workbook.close()
    database_path = tmp_path / "fleet.db"
    import_fleet_workbook(workbook_path, database_path)
    return database_path


def test_apply_correction_recalculates_ready_proposal(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="091",
        document_type="rp",
        controlling_date="07/07/2026",
        unit_folders_root=_unit_root(tmp_path),
    )
    assert corrected["status"] == "ready_for_review"
    assert corrected["reasons"] == []
    assert corrected["unit"] == "91"
    assert corrected["document_type"] == "RP"
    assert corrected["controlling_date"] == "2026-07-07"
    assert corrected["proposed_filename"] == "91_RP_07-07-2026.pdf"
    assert corrected["proposed_destination"].endswith(
        "Unit_91\\004_Maintenance_Records\\91_RP_07-07-2026.pdf"
    )
    assert corrected["manually_corrected"] is True


def test_apply_correction_does_not_mark_unchanged_review_fields_manual(tmp_path):
    first = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="07/07/2026",
        unit_folders_root=_unit_root(tmp_path),
    )
    first.pop("manually_corrected")

    unchanged = apply_correction(
        first,
        unit="091",
        document_type="rp",
        controlling_date="7/7/26",
        unit_folders_root=_unit_root(tmp_path),
    )

    assert unchanged["manually_corrected"] is False


def test_additional_page_gets_distinct_filename_and_can_be_approved(tmp_path):
    unit_root = _unit_root(tmp_path)
    base_destination = (
        unit_root / "Unit_91" / "004_Maintenance_Records" / "91_RP_07-07-2026.pdf"
    )
    base_destination.write_bytes(b"existing-first-page")
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="07/07/2026",
        page_suffix="pg2",
        unit_folders_root=unit_root,
    )

    assert corrected["page_suffix"] == "PG2"
    assert corrected["proposed_filename"] == "91_RP_07-07-2026_PG2.pdf"
    assert Path(corrected["proposed_destination"]) != base_destination

    approved = approve_document(
        corrected,
        audit_path=tmp_path / "Review" / "audit.jsonl",
        unit_folders_root=unit_root,
        incoming_folder=Path(corrected["source_file"]).parent,
    )

    assert approved["status"] == "approved"
    assert Path(approved["approved_destination"]).read_bytes() == b"%PDF-verified-source"
    assert base_destination.read_bytes() == b"existing-first-page"


@pytest.mark.parametrize("page_suffix", ["PG1", "PAGE2", "2", "PG0"])
def test_apply_correction_rejects_invalid_additional_page_suffix(tmp_path, page_suffix):
    with pytest.raises(ReviewValidationError, match="PG2"):
        apply_correction(
            _result(tmp_path),
            unit="91",
            document_type="RP",
            controlling_date="07/07/2026",
            page_suffix=page_suffix,
            unit_folders_root=_unit_root(tmp_path),
        )


@pytest.mark.parametrize(
    ("document_type", "expected_filename"),
    [
        ("TITLE", "91_TITLE_04-15-2026.pdf"),
        ("CERTORIGIN", "91_CERTORIGIN_04-15-2026.pdf"),
    ],
)
def test_registration_family_types_keep_distinct_filenames(
    tmp_path, document_type, expected_filename
):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type=document_type,
        controlling_date="04/15/2026",
        unit_folders_root=_unit_root(tmp_path),
    )

    assert corrected["document_type"] == document_type
    assert corrected["proposed_filename"] == expected_filename
    assert Path(corrected["proposed_destination"]).parent.name == "003_Registration"


def test_apply_correction_creates_standard_folder_for_verified_existing_unit(tmp_path):
    unit_root = tmp_path / "units"
    unit_root.mkdir()
    audit_path = tmp_path / "Review" / "audit.jsonl"

    corrected = apply_correction(
        _result(tmp_path),
        unit="122",
        document_type="INS",
        controlling_date="07/23/2026",
        unit_folders_root=unit_root,
        database_path=_fleet_database(tmp_path),
        audit_path=audit_path,
    )

    assert corrected["status"] == "ready_for_review"
    assert corrected["asset_owner"] is None
    assert Path(corrected["proposed_destination"]).parent.name == "002_Insurance"
    assert sorted(path.name for path in (unit_root / "Unit_122").iterdir()) == [
        "001_Annual_DOT",
        "002_Insurance",
        "003_Registration",
        "004_Maintenance_Records",
    ]
    events = [json.loads(line)["event"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert events == ["unit_folder_creation_started", "unit_folder_created"]


def test_apply_correction_does_not_create_folder_for_unknown_unit(tmp_path):
    unit_root = tmp_path / "units"
    unit_root.mkdir()

    with pytest.raises(ReviewValidationError, match="not uniquely verified"):
        apply_correction(
            _result(tmp_path),
            unit="999",
            document_type="INS",
            controlling_date="07/23/2026",
            unit_folders_root=unit_root,
            database_path=_fleet_database(tmp_path),
            audit_path=tmp_path / "Review" / "audit.jsonl",
        )

    assert not (unit_root / "Unit_999").exists()


def test_folder_creation_audit_failure_rolls_back_and_reports_correction_error(monkeypatch, tmp_path):
    unit_root = tmp_path / "units"
    unit_root.mkdir()
    original_append = assets._append_asset_audit

    def fail_final_audit(path, event, asset, **details):
        if event == "unit_folder_created":
            raise OSError("forced audit failure")
        return original_append(path, event, asset, **details)

    monkeypatch.setattr(assets, "_append_asset_audit", fail_final_audit)
    with pytest.raises(ReviewValidationError, match="could not be created"):
        apply_correction(
            _result(tmp_path),
            unit="122",
            document_type="INS",
            controlling_date="07/23/2026",
            unit_folders_root=unit_root,
            database_path=_fleet_database(tmp_path),
            audit_path=tmp_path / "Review" / "audit.jsonl",
        )

    assert not (unit_root / "Unit_122").exists()
    assert not list(unit_root.glob(".dotdocs-*"))


@pytest.mark.parametrize(
    "entered_date",
    ["8/8/26", "08/8/2026", "8/08/26", "08/08/26", "8-8-26"],
)
def test_apply_correction_accepts_short_equivalent_date_formats(tmp_path, entered_date):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date=entered_date,
        unit_folders_root=_unit_root(tmp_path),
    )

    assert corrected["controlling_date"] == "2026-08-08"
    assert corrected["proposed_filename"] == "91_RP_08-08-2026.pdf"


@pytest.mark.parametrize(
    ("unit", "document_type", "controlling_date", "message"),
    [
        ("", "RP", "07/07/2026", "unit"),
        ("91", "OTHER", "07/07/2026", "document type"),
        ("91", "RP", "not-a-date", "date"),
        ("999", "RP", "07/07/2026", "folder"),
    ],
)
def test_apply_correction_rejects_invalid_values(
    tmp_path, unit, document_type, controlling_date, message
):
    with pytest.raises(ReviewValidationError, match=message):
        apply_correction(
            _result(tmp_path),
            unit=unit,
            document_type=document_type,
            controlling_date=controlling_date,
            unit_folders_root=_unit_root(tmp_path),
        )


def test_approve_document_copies_preserves_source_and_writes_audit(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    audit_path = tmp_path / "Review" / "audit.jsonl"

    approved = approve_document(corrected, audit_path=audit_path, unit_folders_root=_unit_root(tmp_path), incoming_folder=Path(corrected["source_file"]).parent)

    source = Path(corrected["source_file"])
    destination = Path(corrected["proposed_destination"])
    assert source.exists()
    assert destination.read_bytes() == source.read_bytes()
    assert approved["status"] == "approved"
    assert approved["approved_destination"] == str(destination)
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["event"] == "approved"
    assert entries[-1]["source_file"] == str(source)
    assert entries[-1]["destination"] == str(destination)


def test_approve_document_never_overwrites_existing_destination(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    destination = Path(corrected["proposed_destination"])
    destination.write_bytes(b"existing-production-file")
    audit_path = tmp_path / "Review" / "audit.jsonl"

    with pytest.raises(ApprovalError, match="already exists"):
        approve_document(corrected, audit_path=audit_path, unit_folders_root=_unit_root(tmp_path), incoming_folder=Path(corrected["source_file"]).parent)

    assert destination.read_bytes() == b"existing-production-file"
    assert Path(corrected["source_file"]).exists()
    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["event"] == "approval_failed"


def test_mark_duplicate_archives_incoming_pdf_and_preserves_existing_production_file(tmp_path):
    result = _result(tmp_path)
    unit_root = _unit_root(tmp_path)
    destination = unit_root / "Unit_91" / "003_Registration" / "91_REG_08-31-2027.pdf"
    destination.write_bytes(b"existing-production-document")
    audit_path = tmp_path / "Review" / "audit.jsonl"

    duplicate = mark_duplicate_document(
        result,
        unit="91",
        document_type="REG",
        controlling_date="08/31/2027",
        audit_path=audit_path,
        unit_folders_root=unit_root,
        incoming_folder=Path(result["source_file"]).parent,
        processed_folder=tmp_path / "Processed",
    )

    archive = tmp_path / "Processed" / "Duplicates" / "scan.pdf"
    assert duplicate["status"] == "duplicate"
    assert duplicate["duplicate_destination"] == str(destination)
    assert duplicate["duplicate_archived_file"] == str(archive)
    assert archive.read_bytes() == b"%PDF-verified-source"
    assert not Path(result["source_file"]).exists()
    assert destination.read_bytes() == b"existing-production-document"
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["duplicate_mark_started", "marked_duplicate"]


def test_mark_not_dot_moves_pdf_to_exceptions_and_audits_action(tmp_path):
    result = _result(tmp_path)
    audit_path = tmp_path / "Review" / "audit.jsonl"

    not_dot = mark_not_dot_document(
        result,
        audit_path=audit_path,
        incoming_folder=Path(result["source_file"]).parent,
        exceptions_folder=tmp_path / "Exceptions",
    )

    archive = tmp_path / "Exceptions" / "Not DOT" / "scan.pdf"
    assert not_dot["status"] == "not_dot"
    assert not_dot["reasons"] == ["REMOVED_FROM_DOT_WORKFLOW"]
    assert not_dot["not_dot_archived_file"] == str(archive)
    assert not_dot["proposed_filename"] is None
    assert not_dot["proposed_destination"] is None
    assert archive.read_bytes() == b"%PDF-verified-source"
    assert not Path(result["source_file"]).exists()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["not_dot_mark_started", "marked_not_dot"]


def test_mark_not_dot_uses_numbered_archive_name_without_overwriting(tmp_path):
    result = _result(tmp_path)
    archive_folder = tmp_path / "Exceptions" / "Not DOT"
    archive_folder.mkdir(parents=True)
    existing = archive_folder / "scan.pdf"
    existing.write_bytes(b"existing-non-dot-document")

    not_dot = mark_not_dot_document(
        result,
        audit_path=tmp_path / "Review" / "audit.jsonl",
        incoming_folder=Path(result["source_file"]).parent,
        exceptions_folder=tmp_path / "Exceptions",
    )

    archive = archive_folder / "scan_2.pdf"
    assert not_dot["not_dot_archived_file"] == str(archive)
    assert archive.read_bytes() == b"%PDF-verified-source"
    assert existing.read_bytes() == b"existing-non-dot-document"


@pytest.mark.parametrize("archived_status", ["duplicate", "not_dot"])
def test_restore_archived_document_moves_pdf_back_to_active_review(tmp_path, archived_status):
    result = _result(tmp_path)
    if archived_status == "duplicate":
        unit_root = _unit_root(tmp_path)
        destination = unit_root / "Unit_91" / "003_Registration" / "91_REG_08-31-2027.pdf"
        destination.write_bytes(b"existing-production-document")
        archived = mark_duplicate_document(
            result,
            unit="91",
            document_type="REG",
            controlling_date="08/31/2027",
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=unit_root,
            incoming_folder=Path(result["source_file"]).parent,
            processed_folder=tmp_path / "Processed",
        )
    else:
        archived = mark_not_dot_document(
            result,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            incoming_folder=Path(result["source_file"]).parent,
            exceptions_folder=tmp_path / "Exceptions",
        )

    restored = restore_archived_document(
        archived,
        audit_path=tmp_path / "Review" / "audit.jsonl",
        incoming_folder=Path(result["source_file"]).parent,
        processed_folder=tmp_path / "Processed",
        exceptions_folder=tmp_path / "Exceptions",
    )

    source = Path(result["source_file"])
    assert source.read_bytes() == b"%PDF-verified-source"
    assert restored["status"] == "needs_review"
    assert restored["reasons"] == ["RESTORED_TO_ACTIVE_REVIEW"]
    assert restored.get("duplicate_archived_file") is None
    assert restored.get("not_dot_archived_file") is None
    assert restored["proposed_filename"] is None
    assert restored["proposed_destination"] is None
    events = [
        json.loads(line)["event"]
        for line in (tmp_path / "Review" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-2:] == ["restore_to_active_started", "restored_to_active"]


def test_mark_duplicate_rejects_when_expected_production_file_is_missing(tmp_path):
    result = _result(tmp_path)

    with pytest.raises(ReviewValidationError, match="production file does not exist"):
        mark_duplicate_document(
            result,
            unit="91",
            document_type="REG",
            controlling_date="08/31/2027",
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(result["source_file"]).parent,
            processed_folder=tmp_path / "Processed",
        )

    assert Path(result["source_file"]).is_file()
    assert not (tmp_path / "Processed" / "Duplicates").exists()


def test_mark_duplicate_restores_incoming_pdf_if_final_audit_fails(monkeypatch, tmp_path):
    result = _result(tmp_path)
    unit_root = _unit_root(tmp_path)
    destination = unit_root / "Unit_91" / "003_Registration" / "91_REG_08-31-2027.pdf"
    destination.write_bytes(b"existing-production-document")
    audit_path = tmp_path / "Review" / "audit.jsonl"
    original_append = review_module._append_audit

    def fail_final_audit(path, entry):
        if entry.get("event") == "marked_duplicate":
            raise OSError("forced audit failure")
        return original_append(path, entry)

    monkeypatch.setattr(review_module, "_append_audit", fail_final_audit)
    with pytest.raises(ReviewValidationError, match="left in Incoming"):
        mark_duplicate_document(
            result,
            unit="91",
            document_type="REG",
            controlling_date="08/31/2027",
            audit_path=audit_path,
            unit_folders_root=unit_root,
            incoming_folder=Path(result["source_file"]).parent,
            processed_folder=tmp_path / "Processed",
        )

    assert Path(result["source_file"]).read_bytes() == b"%PDF-verified-source"
    assert not list((tmp_path / "Processed" / "Duplicates").glob("*.pdf"))
    assert destination.read_bytes() == b"existing-production-document"
    events = [json.loads(line)["event"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert events == ["duplicate_mark_started", "duplicate_mark_failed"]


def test_approve_document_rejects_unreviewed_exception(tmp_path):
    result = _result(tmp_path)
    with pytest.raises(ApprovalError, match="ready"):
        approve_document(result, audit_path=tmp_path / "audit.jsonl", unit_folders_root=_unit_root(tmp_path), incoming_folder=Path(result["source_file"]).parent)


def test_approve_document_rejects_tampered_destination_outside_unit_root(tmp_path):
    unit_root = _unit_root(tmp_path)
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=unit_root,
    )
    outside = tmp_path / "outside" / corrected["proposed_filename"]
    outside.parent.mkdir()
    corrected["proposed_destination"] = str(outside)

    with pytest.raises(ApprovalError, match="does not match"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=unit_root,
            incoming_folder=Path(corrected["source_file"]).parent,
        )

    assert not outside.exists()


def test_approve_rejects_source_changed_after_review(tmp_path):
    result = _result(tmp_path)
    unit_root = _unit_root(tmp_path)
    corrected = apply_correction(result, unit="91", document_type="RP", controlling_date="2026-07-07", unit_folders_root=unit_root)
    Path(corrected["source_file"]).write_bytes(b"replacement-content")
    with pytest.raises(ApprovalError, match="changed since review"):
        approve_document(corrected, audit_path=tmp_path / "audit.jsonl", unit_folders_root=unit_root, incoming_folder=Path(corrected["source_file"]).parent)


def test_review_session_round_trip(tmp_path):
    from dotdocs.review import load_review_session, save_review_session

    path = tmp_path / "Review" / "active-session.json"
    results = [_result(tmp_path)]
    save_review_session(path, results)

    assert load_review_session(path) == results
    assert not (path.parent / (path.name + ".tmp")).exists()


def test_record_correction_appends_before_and_after_values(tmp_path):
    from dotdocs.review import record_correction

    before = _result(tmp_path)
    after = dict(before, unit="91", controlling_date="2026-07-07", status="ready_for_review")
    audit_path = tmp_path / "Review" / "audit.jsonl"
    record_correction(audit_path, before, after)

    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["event"] == "correction_saved"
    assert entry["before"]["controlling_date"] is None
    assert entry["after"]["controlling_date"] == "2026-07-07"


def test_record_correction_skips_no_op_review_fields(tmp_path):
    from dotdocs.review import record_correction

    before = dict(
        _result(tmp_path),
        status="needs_review",
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        page_suffix=None,
    )
    after = dict(before, status="ready_for_review", reasons=[])
    audit_path = tmp_path / "Review" / "audit.jsonl"

    assert record_correction(audit_path, before, after) is False
    assert not audit_path.exists()


def test_record_asset_created_writes_owner_and_identifiers(tmp_path):
    audit_path = tmp_path / "Review" / "audit.jsonl"
    record_asset_created(
        audit_path,
        {"unit": "305", "asset_owner": "Farm Asset", "vin": "VIN305", "plate": "FARM305"},
    )

    entry = json.loads(audit_path.read_text(encoding="utf-8").splitlines()[-1])
    assert entry == {
        "asset_owner": "Farm Asset",
        "event": "asset_created",
        "plate": "FARM305",
        "timestamp_utc": entry["timestamp_utc"],
        "unit": "305",
        "vin": "VIN305",
    }
