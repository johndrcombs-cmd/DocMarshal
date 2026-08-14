from pathlib import Path

import fitz
import pytest
from openpyxl import Workbook

from dotdocs.database import import_fleet_workbook
import dotdocs.processor as processor
from dotdocs.processor import SourceChangedDuringAnalysisError, analyze_pdf, find_unit_folder
from dotdocs.tools_database import create_tool


def _database(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    database_path = tmp_path / "fleet.db"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Unit Type", "Year", "Make", "Model", "Type", "Tag", "Vin"])
    sheet.append(["097", "Truck", 2010, "CHEVY", "SILVERADO", "3500", "V32192", "1GB3KZBK9AF141680"])
    workbook.save(workbook_path)
    import_fleet_workbook(workbook_path, database_path)
    return database_path


def test_analysis_rejects_source_that_changes_between_snapshot_and_completion(monkeypatch, tmp_path):
    database = _database(tmp_path)
    unit_root = tmp_path / "Units"
    unit_root.mkdir()
    pdf = tmp_path / "scan.pdf"
    _searchable_pdf(
        pdf,
        "SERVICE OKLAHOMA CERTIFICATE OF REGISTRATION VIN 1GB3KZBK9AF141680 Reg Expires 3/31/2026",
    )
    real_fingerprint = processor.source_fingerprint
    source_reads = 0

    def changing_fingerprint(path):
        nonlocal source_reads
        fingerprint = real_fingerprint(path)
        if Path(path) == pdf:
            source_reads += 1
            if source_reads >= 3:
                return ("0" * 64, fingerprint[1])
        return fingerprint

    monkeypatch.setattr(processor, "source_fingerprint", changing_fingerprint)

    with pytest.raises(SourceChangedDuringAnalysisError):
        analyze_pdf(pdf, database, unit_root)


def test_analysis_intake_metadata_is_derived_from_immutable_snapshot(monkeypatch, tmp_path):
    pdf = tmp_path / "registration.pdf"
    _searchable_pdf(
        pdf,
        "SERVICE OKLAHOMA CERTIFICATE OF REGISTRATION VIN 1GB3KZBK9AF141680 Reg Expires 3/31/2026",
    )
    database = _database(tmp_path)
    unit_root = tmp_path / "units"
    (unit_root / "Unit_101" / "Registration").mkdir(parents=True)
    original_snapshot = processor.source_snapshot
    snapshot_paths = []

    def poison_live_snapshot(path):
        snapshot_paths.append(Path(path))
        if Path(path) == pdf:
            return {
                "source_size": 1,
                "source_mtime_ns": 2,
                "source_quick_signature": "transient-live-version",
            }
        return original_snapshot(path)

    monkeypatch.setattr(processor, "source_snapshot", poison_live_snapshot)

    result = analyze_pdf(pdf, database, unit_root)

    assert pdf not in snapshot_paths
    assert result["source_size"] == pdf.stat().st_size
    assert result["source_mtime_ns"] == pdf.stat().st_mtime_ns
    assert result["source_quick_signature"] != "transient-live-version"


def _searchable_pdf(path, text):
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(72, 72, 540, 300), text, fontsize=10)
    document.save(path)
    document.close()


def test_analyzes_searchable_registration_without_moving_it(tmp_path):
    database = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    destination = unit_root / "Unit_97" / "003_Registration"
    destination.mkdir(parents=True)
    pdf = tmp_path / "scan.pdf"
    _searchable_pdf(
        pdf,
        "SERVICE OKLAHOMA CERTIFICATE OF REGISTRATION VIN 1GB3KZBK9AF141680 Reg Expires 3/31/2026",
    )

    result = analyze_pdf(pdf, database, unit_root)

    assert result["status"] == "ready_for_review"
    assert result["unit"] == "97"
    assert result["document_type"] == "REG"
    assert result["controlling_date"] == "2026-03-31"
    assert result["proposed_filename"] == "97_REG_03-31-2026.pdf"
    assert result["proposed_destination"] == str(destination / "97_REG_03-31-2026.pdf")
    assert pdf.exists()
    assert not (destination / "97_REG_03-31-2026.pdf").exists()


def test_analyzes_searchable_semi_cab_card_as_distinct_registration_document(tmp_path):
    database = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    destination = unit_root / "Unit_97" / "003_Registration"
    destination.mkdir(parents=True)
    pdf = tmp_path / "cab-card.pdf"
    _searchable_pdf(
        pdf,
        "OKLAHOMA APPORTIONED CAB CARD VIN 1GB3KZBK9AF141680 Registration Expires 6/30/2026",
    )

    result = analyze_pdf(pdf, database, unit_root)

    assert result["status"] == "ready_for_review"
    assert result["document_type"] == "CAB"
    assert result["controlling_date"] == "2026-06-30"
    assert result["proposed_filename"] == "97_CAB_06-30-2026.pdf"
    assert result["proposed_destination"] == str(destination / "97_CAB_06-30-2026.pdf")


def test_routes_image_only_pdf_to_review_without_guessing(tmp_path):
    database = _database(tmp_path)
    pdf = tmp_path / "image-only.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()

    result = analyze_pdf(pdf, database, tmp_path)

    assert result["status"] == "needs_review"
    assert result["reasons"] == ["NO_SEARCHABLE_TEXT"]


def test_find_unit_folder_accepts_only_exact_canonical_folder(tmp_path):
    root = tmp_path / "In Use"
    archive = root / "Archive_Unit_305_DO_NOT_FILE"
    canonical = root / "Unit_305"
    archive.mkdir(parents=True)
    canonical.mkdir()

    assert find_unit_folder(root, "305") == canonical
    canonical.rmdir()
    assert find_unit_folder(root, "305") is None


def test_analyzes_calibration_certificate_as_tool_subject_without_unit_matching(tmp_path):
    database = _database(tmp_path)
    create_tool(database, {
        "tool_id": "CAL-001", "description": "Pressure gauge", "serial_number": "SN-441",
    })
    tool_root = tmp_path / "Tool Binders"
    destination = tool_root / "Tool_CAL-001" / "001_Calibration_Certifications"
    destination.mkdir(parents=True)
    pdf = tmp_path / "calibration.pdf"
    _searchable_pdf(
        pdf,
        "ISO 17025 CERTIFICATE OF CALIBRATION Serial No. SN-441 Calibration Date 9/30/2026 Due Date 9/30/2027",
    )

    result = analyze_pdf(pdf, database, tmp_path / "Fleet", tool_folders_root=tool_root)

    assert result["status"] == "ready_for_review"
    assert result["subject_type"] == "tool"
    assert result["subject_id"] == "CAL-001"
    assert result["unit"] is None
    assert result["document_type"] == "CAL"
    assert result["controlling_date"] == "2027-09-30"
    assert result["proposed_filename"] == "CAL-001_CAL_09-30-2027.pdf"
    assert result["proposed_destination"] == str(destination / "CAL-001_CAL_09-30-2027.pdf")


def test_calibration_certificate_with_unlabeled_model_text_does_not_guess_tool(tmp_path):
    database = _database(tmp_path)
    create_tool(database, {"tool_id": "CAL-001", "description": "Pressure gauge", "model": "441"})
    pdf = tmp_path / "calibration.pdf"
    _searchable_pdf(pdf, "CERTIFICATE OF CALIBRATION model 441 Due Date 9/30/2027")

    result = analyze_pdf(pdf, database, tmp_path, tool_folders_root=tmp_path / "Tools")

    assert result["subject_type"] == "tool"
    assert result["subject_id"] is None
    assert "TOOL_UNMATCHED" in result["reasons"]
