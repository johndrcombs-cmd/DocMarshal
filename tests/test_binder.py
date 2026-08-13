import hashlib
from pathlib import Path

import fitz
import pytest
from openpyxl import Workbook

from dotdocs.binder import (
    BINDER_SECTIONS,
    TOOL_BINDER_SECTIONS,
    advance_binder_position,
    list_binder_documents,
    list_binders,
    list_tool_binders,
    render_binder_page,
    render_pdf_page,
)
from dotdocs.database import import_fleet_workbook
from dotdocs.tools_database import create_tool


def _database(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Unit Type", "Tag", "Vin", "Asset Owner"])
    sheet.append(["91", "Truck", "TAG91", "VIN00091", "Little B's Asset"])
    sheet.append(["305", "Tractor", "TAG305", "VIN00305", "Farm Asset"])
    workbook.save(workbook_path)
    database = tmp_path / "fleet.db"
    import_fleet_workbook(workbook_path, database)
    return database


def test_lists_ownership_aware_binder_spines_and_availability(tmp_path):
    database = _database(tmp_path)
    company = tmp_path / "Company"
    farm = tmp_path / "Farm"
    (company / "Unit_91").mkdir(parents=True)
    farm.mkdir()

    binders = list_binders(database, company, farm)

    assert [(item["unit"], item["owner"], item["available"]) for item in binders] == [
        ("91", "Little B's Asset", True),
        ("305", "Farm Asset", False),
    ]
    assert binders[1]["folder"] == farm / "Unit_305"


def test_lists_tool_binders_with_calibration_status_and_canonical_folders(tmp_path):
    database = tmp_path / "fleet.db"
    current = create_tool(database, {
        "tool_id": "CAL-001", "description": "Pressure gauge", "serial_number": "SN-1",
        "calibration_required": True,
    })
    create_tool(database, {
        "tool_id": "CAL-002", "description": "Inactive wrench", "active": False,
        "calibration_required": True,
    })
    root = tmp_path / "Tools"
    (root / "Tool_CAL-001").mkdir(parents=True)

    binders = list_tool_binders(database, root)

    assert len(binders) == 1
    assert binders[0]["tool_id"] == "CAL-001"
    assert binders[0]["description"] == "Pressure gauge"
    assert binders[0]["folder"] == root / "Tool_CAL-001"
    assert binders[0]["available"] is True
    assert binders[0]["status"].code == "no_date"
    assert current["id"] == binders[0]["database_id"]
    assert TOOL_BINDER_SECTIONS == (
        ("Calibration / Certification", "001_Calibration_Certifications"),
        ("Misc", "002_Misc"),
    )


def test_lists_only_direct_pdfs_in_a_canonical_binder_tab(tmp_path):
    unit = tmp_path / "Unit_91"
    section = unit / "003_Registration"
    section.mkdir(parents=True)
    (section / "registration.pdf").write_bytes(b"pdf")
    (section / "notes.txt").write_text("private", encoding="utf-8")
    nested = section / "nested"
    nested.mkdir()
    (nested / "hidden.pdf").write_bytes(b"pdf")

    assert list_binder_documents(unit, "003_Registration") == [section / "registration.pdf"]
    with pytest.raises(ValueError, match="binder section"):
        list_binder_documents(unit, "..")
    assert {folder for _label, folder in BINDER_SECTIONS} == {
        "001_Annual_DOT",
        "002_Insurance",
        "003_Registration",
        "004_Maintenance_Records",
        "005_Misc",
    }


def test_renders_one_safe_pdf_page_at_a_time(tmp_path):
    section = tmp_path / "Unit_91" / "001_Annual_DOT"
    section.mkdir(parents=True)
    pdf_path = section / "inspection.pdf"
    document = fitz.open()
    document.new_page(width=612, height=792).insert_text((72, 72), "Page One")
    document.new_page(width=612, height=792).insert_text((72, 72), "Page Two")
    document.save(pdf_path)
    document.close()

    rendered = render_binder_page(pdf_path, section, 1, max_width=500, max_height=500)

    assert rendered["page_index"] == 1
    assert rendered["page_count"] == 2
    assert rendered["png"].startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="directly inside"):
        render_binder_page(pdf_path, tmp_path, 0)
    with pytest.raises(IndexError, match="page"):
        render_binder_page(pdf_path, section, 2)


def test_render_pdf_page_supports_safe_read_only_incoming_preview(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    pdf = incoming / "review.pdf"
    document = fitz.open()
    document.new_page(width=320, height=480)
    document.new_page(width=320, height=480)
    document.save(pdf)
    document.close()

    rendered = render_pdf_page(pdf, incoming, 1, max_width=240, max_height=240)

    assert rendered["page_index"] == 1
    assert rendered["page_count"] == 2
    assert rendered["png"].startswith(b"\x89PNG")


def test_render_pdf_page_rotates_preview_without_modifying_source(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    pdf = incoming / "upside-down.pdf"
    document = fitz.open()
    document.new_page(width=200, height=400)
    document.save(pdf)
    document.close()
    before = hashlib.sha256(pdf.read_bytes()).hexdigest()

    rendered = render_pdf_page(
        pdf,
        incoming,
        0,
        max_width=800,
        max_height=800,
        rotation=90,
    )

    assert rendered["width"] == 800
    assert rendered["height"] == 400
    assert rendered["rotation"] == 90
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == before


def test_render_pdf_page_rejects_non_quarter_turn_rotation(tmp_path):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    pdf = incoming / "review.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()

    with pytest.raises(ValueError, match="rotation"):
        render_pdf_page(pdf, incoming, 0, rotation=45)


def test_binder_navigation_advances_through_pages_documents_and_categories():
    pages = ((1,), (2, 1), (), (1,))

    assert advance_binder_position(pages, 0, 0, 0, 1) == (1, 0, 0)
    assert advance_binder_position(pages, 1, 0, 0, 1) == (1, 0, 1)
    assert advance_binder_position(pages, 1, 0, 1, 1) == (1, 1, 0)
    assert advance_binder_position(pages, 1, 1, 0, 1) == (2, None, None)
    assert advance_binder_position(pages, 2, None, None, 1) == (3, 0, 0)
    assert advance_binder_position(pages, 3, 0, 0, -1) == (2, None, None)


def test_render_binder_page_supports_relative_zoom(tmp_path):
    section = tmp_path / "Unit_7" / "001_Annual_DOT"
    section.mkdir(parents=True)
    pdf_path = section / "inspection.pdf"
    document = fitz.open()
    document.new_page(width=300, height=400)
    document.save(pdf_path)
    document.close()

    fitted = render_binder_page(pdf_path, section, 0, max_width=300, max_height=400, zoom_factor=1.0)
    zoomed = render_binder_page(pdf_path, section, 0, max_width=300, max_height=400, zoom_factor=2.0)

    assert zoomed["width"] == fitted["width"] * 2
    assert zoomed["height"] == fitted["height"] * 2
    with pytest.raises(ValueError, match="zoom"):
        render_binder_page(pdf_path, section, 0, zoom_factor=0)
