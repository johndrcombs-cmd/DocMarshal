import json

import fitz
from openpyxl import Workbook

from dotdocs.database import import_fleet_workbook
from dotdocs.runner import process_inbox


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


def _pdf(path, text=None):
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_textbox(fitz.Rect(72, 72, 540, 300), text, fontsize=10)
    document.save(path)
    document.close()


def test_processes_inbox_to_json_and_csv_without_moving_sources(tmp_path):
    database = _database(tmp_path)
    incoming = tmp_path / "Incoming"
    review = tmp_path / "Review"
    unit_root = tmp_path / "In Use"
    (unit_root / "Unit_97" / "003_Registration").mkdir(parents=True)
    incoming.mkdir()

    searchable = incoming / "searchable.pdf"
    image_only = incoming / "image-only.pdf"
    _pdf(searchable, "CERTIFICATE OF REGISTRATION VIN 1GB3KZBK9AF141680 Reg Expires 3/31/2026")
    _pdf(image_only)

    summary = process_inbox(incoming, review, database, unit_root, report_name="test-review")

    assert summary["files_scanned"] == 2
    assert summary["ready_for_review"] == 1
    assert summary["needs_review"] == 1
    assert searchable.exists() and image_only.exists()
    assert (review / "test-review.json").exists()
    assert (review / "test-review.csv").exists()
    report = json.loads((review / "test-review.json").read_text(encoding="utf-8"))
    assert {item["status"] for item in report} == {"ready_for_review", "needs_review"}
