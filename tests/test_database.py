import sqlite3

from openpyxl import Workbook

from dotdocs.database import find_units_by_identifier, import_fleet_workbook


def test_imports_units_and_preserves_ambiguous_plate_matches(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    database_path = tmp_path / "fleet.db"

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "dot trucks and trailers"
    sheet.append(["Unit #", "Unit Type", "Year", "Make", "Model", "Type", "Tag", "Vin"])
    sheet.append(["097", "Truck", 2010, "CHEVY", "SILVERADO", "3500", "V32192", "1GB3KZBK9AF141680"])
    sheet.append(["218", "Truck", 2022, "GMC", "SIERRA", "3500", "V32265", "1GD49SE78NF297303"])
    sheet.append(["093", "Truck", 2009, "CHEVY", "PICKUP", "K3500", "V32265", "1GBJK74KX9E130973"])
    workbook.save(workbook_path)

    stats = import_fleet_workbook(workbook_path, database_path)

    assert stats["records_imported"] == 3
    assert stats["ambiguous_plates"] == {"V32265": ["93", "218"]}
    assert find_units_by_identifier(database_path, vin="1gb3-kzbk9-af141680") == ["97"]
    assert find_units_by_identifier(database_path, plate="v 32265") == ["93", "218"]

    with sqlite3.connect(database_path) as connection:
        unit = connection.execute(
            "SELECT display_unit, normalized_unit, vin, plate FROM units WHERE normalized_unit = '97'"
        ).fetchone()
    assert unit == ("097", "97", "1GB3KZBK9AF141680", "V32192")


def test_database_is_not_left_locked_after_lookup(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    database_path = tmp_path / "fleet.db"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Unit Type", "Year", "Make", "Model", "Type", "Tag", "Vin"])
    sheet.append(["097", "Truck", 2010, "CHEVY", "SILVERADO", "3500", "V32192", "1GB3KZBK9AF141680"])
    workbook.save(workbook_path)

    import_fleet_workbook(workbook_path, database_path)
    assert find_units_by_identifier(database_path, vin="1GB3KZBK9AF141680") == ["97"]

    database_path.unlink()
    assert not database_path.exists()
