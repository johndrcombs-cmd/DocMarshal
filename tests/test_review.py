import json
import hashlib
from pathlib import Path

import pytest
from openpyxl import Workbook

import dotdocs.assets as assets
import dotdocs.review as review_module
from dotdocs.database import import_fleet_workbook
from dotdocs.review import (
    NON_DOT_DOCUMENT_TYPES,
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
        approved_folder=tmp_path / "Approved",
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
        ("CAB", "91_CAB_04-15-2026.pdf"),
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


def test_misc_correction_routes_to_canonical_misc_folder(tmp_path):
    unit_root = _unit_root(tmp_path)
    misc_folder = unit_root / "Unit_91" / "005_Misc"
    misc_folder.mkdir()

    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="MISC",
        controlling_date="08/11/2026",
        unit_folders_root=unit_root,
    )

    assert corrected["document_type"] == "MISC"
    assert corrected["proposed_filename"] == "91_MISC_08-11-2026.pdf"
    assert Path(corrected["proposed_destination"]).parent == misc_folder


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
        "005_Misc",
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


def test_approve_document_copies_and_archives_source_and_writes_audit(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    audit_path = tmp_path / "Review" / "audit.jsonl"

    approved_folder = tmp_path / "Approved"
    approved = approve_document(
        corrected,
        audit_path=audit_path,
        unit_folders_root=_unit_root(tmp_path),
        incoming_folder=Path(corrected["source_file"]).parent,
        approved_folder=approved_folder,
    )

    source = Path(corrected["source_file"])
    destination = Path(corrected["proposed_destination"])
    archive = approved_folder / source.name
    assert not source.exists()
    assert destination.read_bytes() == archive.read_bytes()
    assert approved["status"] == "approved"
    assert approved["approved_destination"] == str(destination)
    assert approved["approved_archived_file"] == str(archive)
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert entries[-1]["event"] == "approved"
    assert entries[-1]["source_file"] == str(source)
    assert entries[-1]["destination"] == str(destination)
    assert entries[-1]["archived_file"] == str(archive)


def test_approve_document_rejects_approved_folder_equal_to_incoming_before_side_effects(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    source = Path(corrected["source_file"])
    destination = Path(corrected["proposed_destination"])
    audit_path = tmp_path / "Review" / "audit.jsonl"

    with pytest.raises(ApprovalError, match="must be different"):
        approve_document(
            corrected,
            audit_path=audit_path,
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=source.parent,
            approved_folder=source.parent,
        )

    assert source.is_file()
    assert not destination.exists()
    assert not audit_path.exists()


def test_approve_document_uses_collision_safe_archive_name(tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    approved_folder = tmp_path / "Approved"
    approved_folder.mkdir()
    existing = approved_folder / "scan.pdf"
    existing.write_bytes(b"existing-approved-source")

    approved = approve_document(
        corrected,
        audit_path=tmp_path / "Review" / "audit.jsonl",
        unit_folders_root=_unit_root(tmp_path),
        incoming_folder=Path(corrected["source_file"]).parent,
        approved_folder=approved_folder,
    )

    assert Path(approved["approved_archived_file"]).name == "scan_2.pdf"
    assert existing.read_bytes() == b"existing-approved-source"


def test_approve_document_never_removes_archive_created_by_a_collision_race(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    approved_folder = tmp_path / "Approved"
    source = Path(corrected["source_file"])
    original_rename = Path.rename
    injected = False

    def race_rename(path, target):
        nonlocal injected
        if path == source and Path(target) == approved_folder / "scan.pdf" and not injected:
            injected = True
            approved_folder.mkdir(parents=True, exist_ok=True)
            with (approved_folder / "scan.pdf").open("xb") as other_archive:
                other_archive.write(b"other-process-archive")
            raise FileExistsError(target)
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", race_rename)

    with pytest.raises(ApprovalError):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=approved_folder,
        )

    assert (approved_folder / "scan.pdf").read_bytes() == b"other-process-archive"
    assert Path(corrected["source_file"]).is_file()
    assert not Path(corrected["proposed_destination"]).exists()


def test_approve_document_preserves_source_replaced_at_archive_boundary(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    source = Path(corrected["source_file"])
    approved_folder = tmp_path / "Approved"
    original_rename = Path.rename
    injected = False

    def replace_source_then_rename(path, target):
        nonlocal injected
        if path == source and Path(target).parent == approved_folder and not injected:
            injected = True
            source.unlink()
            source.write_bytes(b"other-process-incoming-file")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", replace_source_then_rename)

    with pytest.raises(ApprovalError, match="source was restored"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=source.parent,
            approved_folder=approved_folder,
        )

    assert source.read_bytes() == b"other-process-incoming-file"
    assert not Path(corrected["proposed_destination"]).exists()
    assert not list(approved_folder.glob("*.pdf"))


def test_approve_document_does_not_delete_replaced_production_file_during_rollback(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    destination = Path(corrected["proposed_destination"])
    original_append = review_module._append_audit

    def replace_destination_then_fail(path, entry):
        if entry.get("event") == "approved":
            destination.unlink()
            destination.write_bytes(b"other-process-production-file")
            raise OSError("forced final audit failure")
        return original_append(path, entry)

    monkeypatch.setattr(review_module, "_append_audit", replace_destination_then_fail)

    with pytest.raises(ApprovalError, match="rollback could not safely restore every file"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=tmp_path / "Approved",
        )

    assert Path(corrected["source_file"]).is_file()
    assert destination.read_bytes() == b"other-process-production-file"


def test_production_quarantine_verifies_the_atomically_moved_file(monkeypatch, tmp_path):
    path = tmp_path / "production.pdf"
    owned = b"owned-production"
    path.write_bytes(owned)
    identity = path.stat()
    expected_hash = hashlib.sha256(owned).hexdigest()
    original_delete = review_module._delete_owned_quarantine_windows
    injected = False

    def replace_shared_path_before_handle_bound_verification(candidate, *args):
        nonlocal injected
        if ".docmarshal-rollback-" in candidate.name and not injected:
            injected = True
            path.write_bytes(b"other-process-production")
        return original_delete(candidate, *args)

    monkeypatch.setattr(
        review_module,
        "_delete_owned_quarantine_windows",
        replace_shared_path_before_handle_bound_verification,
    )

    review_module._remove_owned_file_via_quarantine(path, identity, expected_hash, len(owned))

    assert path.read_bytes() == b"other-process-production"
    assert not list(tmp_path.glob("*.docmarshal-rollback-*.tmp"))


def test_windows_quarantine_cleanup_uses_handle_bound_delete(monkeypatch, tmp_path):
    path = tmp_path / "production.pdf"
    owned = b"owned-production"
    path.write_bytes(owned)
    identity = path.stat()
    expected_hash = hashlib.sha256(owned).hexdigest()
    calls = []

    monkeypatch.setattr(review_module.os, "name", "nt")
    monkeypatch.setattr(
        review_module,
        "_delete_owned_quarantine_windows",
        lambda quarantine, expected_identity, digest, size: (
            calls.append((quarantine, expected_identity, digest, size)) or True
        ),
    )

    review_module._remove_owned_file_via_quarantine(path, identity, expected_hash, len(owned))

    assert len(calls) == 1
    assert ".docmarshal-rollback-" in calls[0][0].name


def test_windows_file_disposition_info_uses_documented_one_byte_boolean():
    assert review_module.ctypes.sizeof(review_module._FILE_DISPOSITION_INFO) == 1


def test_windows_handle_is_closed_when_crt_descriptor_conversion_fails(monkeypatch, tmp_path):
    path = tmp_path / "owned.tmp"
    data = b"owned-production"
    path.write_bytes(data)
    identity = path.stat()
    monkeypatch.setattr(
        review_module,
        "_win32_open_osfhandle",
        lambda _handle: (_ for _ in ()).throw(OSError("forced conversion failure")),
    )

    with pytest.raises(OSError, match="forced conversion failure"):
        review_module._delete_owned_quarantine_windows(
            path, identity, hashlib.sha256(data).hexdigest(), len(data)
        )

    path.unlink()
    assert not path.exists()


def test_windows_descriptor_is_closed_when_fdopen_fails(monkeypatch, tmp_path):
    path = tmp_path / "owned.tmp"
    data = b"owned-production"
    path.write_bytes(data)
    identity = path.stat()
    monkeypatch.setattr(
        review_module,
        "_fdopen_binary_read",
        lambda _descriptor: (_ for _ in ()).throw(OSError("forced fdopen failure")),
    )

    with pytest.raises(OSError, match="forced fdopen failure"):
        review_module._delete_owned_quarantine_windows(
            path, identity, hashlib.sha256(data).hexdigest(), len(data)
        )

    path.unlink()
    assert not path.exists()


def test_approve_document_does_not_delete_replaced_archive_during_rollback(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    approved_folder = tmp_path / "Approved"
    archive = approved_folder / "scan.pdf"
    original_append = review_module._append_audit

    def replace_archive_then_fail(path, entry):
        if entry.get("event") == "approved":
            archive.unlink()
            archive.write_bytes(b"other-process-approved-file")
            raise OSError("forced final audit failure")
        return original_append(path, entry)

    monkeypatch.setattr(review_module, "_append_audit", replace_archive_then_fail)

    with pytest.raises(ApprovalError, match="rollback could not safely restore every file"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=approved_folder,
        )

    assert archive.read_bytes() == b"other-process-approved-file"
    assert not Path(corrected["proposed_destination"]).exists()


def test_archive_quarantine_verifies_moved_file_without_touching_new_shared_path(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    source = Path(corrected["source_file"])
    approved_folder = tmp_path / "Approved"
    archive = approved_folder / source.name
    original_append = review_module._append_audit
    original_matches = review_module._matches_owned_file
    injected = False

    def fail_final_audit(path, entry):
        if entry.get("event") == "approved":
            raise OSError("forced final audit failure")
        return original_append(path, entry)

    def create_new_archive_during_quarantine_verification(candidate, *args):
        nonlocal injected
        if ".docmarshal-restore-" in candidate.name and not injected:
            injected = True
            archive.write_bytes(b"other-process-approved-file")
        return original_matches(candidate, *args)

    monkeypatch.setattr(review_module, "_append_audit", fail_final_audit)
    monkeypatch.setattr(
        review_module, "_matches_owned_file", create_new_archive_during_quarantine_verification
    )

    with pytest.raises(ApprovalError, match="source was restored"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=source.parent,
            approved_folder=approved_folder,
        )

    assert source.read_bytes() == b"%PDF-verified-source"
    assert archive.read_bytes() == b"other-process-approved-file"
    assert not Path(corrected["proposed_destination"]).exists()


def test_approve_document_reports_persistent_audit_failure_as_approval_error(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )

    monkeypatch.setattr(
        review_module,
        "_append_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("audit unavailable")),
    )

    with pytest.raises(ApprovalError, match="Failure logging also failed"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=tmp_path / "Approved",
        )

    assert Path(corrected["source_file"]).is_file()
    assert not Path(corrected["proposed_destination"]).exists()


def test_approve_document_restores_source_and_removes_copy_when_final_audit_fails(monkeypatch, tmp_path):
    corrected = apply_correction(
        _result(tmp_path),
        unit="91",
        document_type="RP",
        controlling_date="2026-07-07",
        unit_folders_root=_unit_root(tmp_path),
    )
    original_append = review_module._append_audit

    def fail_approved_audit(path, entry):
        if entry.get("event") == "approved":
            raise OSError("forced final audit failure")
        return original_append(path, entry)

    monkeypatch.setattr(review_module, "_append_audit", fail_approved_audit)
    source = Path(corrected["source_file"])
    destination = Path(corrected["proposed_destination"])
    approved_folder = tmp_path / "Approved"

    with pytest.raises(ApprovalError, match="source was restored"):
        approve_document(
            corrected,
            audit_path=tmp_path / "Review" / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=source.parent,
            approved_folder=approved_folder,
        )

    assert source.read_bytes() == b"%PDF-verified-source"
    assert not destination.exists()
    assert not list(approved_folder.glob("*.pdf"))


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
        approve_document(
            corrected,
            audit_path=audit_path,
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=tmp_path / "Approved",
        )

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
        classification="MVR_AUTH",
        audit_path=audit_path,
        incoming_folder=Path(result["source_file"]).parent,
        exceptions_folder=tmp_path / "Exceptions",
    )

    archive = tmp_path / "Exceptions" / "Not DOT" / "scan.pdf"
    assert not_dot["status"] == "not_dot"
    assert not_dot["reasons"] == ["REMOVED_FROM_DOT_WORKFLOW"]
    assert not_dot["non_dot_classification"] == "MVR_AUTH"
    assert not_dot["non_dot_classification_label"] == "MVR Auth"
    assert not_dot["not_dot_archived_file"] == str(archive)
    assert not_dot["proposed_filename"] is None
    assert not_dot["proposed_destination"] is None
    assert archive.read_bytes() == b"%PDF-verified-source"
    assert not Path(result["source_file"]).exists()
    entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert [entry["event"] for entry in entries] == ["not_dot_mark_started", "marked_not_dot"]
    assert all(entry["non_dot_classification"] == "MVR_AUTH" for entry in entries)


def test_not_dot_classifications_are_stable_learning_labels():
    assert NON_DOT_DOCUMENT_TYPES == {
        "MVR_AUTH": "MVR Auth",
        "CALIBRATION_CERT": "Calibration Certificate",
        "TRAINING_DOC": "Training Document",
        "OTHER": "Other / Unclassified",
    }


def test_mark_not_dot_requires_supported_classification_before_moving_source(tmp_path):
    result = _result(tmp_path)

    with pytest.raises(ReviewValidationError, match="classification"):
        mark_not_dot_document(
            result,
            classification="",
            audit_path=tmp_path / "Review" / "audit.jsonl",
            incoming_folder=Path(result["source_file"]).parent,
            exceptions_folder=tmp_path / "Exceptions",
        )

    assert Path(result["source_file"]).is_file()
    assert not (tmp_path / "Exceptions").exists()


def test_mark_not_dot_uses_numbered_archive_name_without_overwriting(tmp_path):
    result = _result(tmp_path)
    archive_folder = tmp_path / "Exceptions" / "Not DOT"
    archive_folder.mkdir(parents=True)
    existing = archive_folder / "scan.pdf"
    existing.write_bytes(b"existing-non-dot-document")

    not_dot = mark_not_dot_document(
        result,
        classification="OTHER",
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
            classification="TRAINING_DOC",
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
        approve_document(
            result,
            audit_path=tmp_path / "audit.jsonl",
            unit_folders_root=_unit_root(tmp_path),
            incoming_folder=Path(result["source_file"]).parent,
            approved_folder=tmp_path / "Approved",
        )


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
            approved_folder=tmp_path / "Approved",
        )

    assert not outside.exists()


def test_approve_rejects_source_changed_after_review(tmp_path):
    result = _result(tmp_path)
    unit_root = _unit_root(tmp_path)
    corrected = apply_correction(result, unit="91", document_type="RP", controlling_date="2026-07-07", unit_folders_root=unit_root)
    Path(corrected["source_file"]).write_bytes(b"replacement-content")
    with pytest.raises(ApprovalError, match="changed since review"):
        approve_document(
            corrected,
            audit_path=tmp_path / "audit.jsonl",
            unit_folders_root=unit_root,
            incoming_folder=Path(corrected["source_file"]).parent,
            approved_folder=tmp_path / "Approved",
        )


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
