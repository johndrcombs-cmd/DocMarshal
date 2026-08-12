import fitz
from openpyxl import Workbook

from dotdocs.database import import_fleet_workbook
from dotdocs.processor import analyze_pdf, find_unit_folder


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
