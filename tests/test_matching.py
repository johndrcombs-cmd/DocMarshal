from openpyxl import Workbook

from dotdocs.database import import_fleet_workbook
from dotdocs.matching import match_units_in_text


def _fleet_database(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    database_path = tmp_path / "fleet.db"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Unit Type", "Year", "Make", "Model", "Type", "Tag", "Vin"])
    sheet.append(["097", "Truck", 2010, "CHEVY", "SILVERADO", "3500", "V32192", "1GB3KZBK9AF141680"])
    sheet.append(["218", "Truck", 2022, "GMC", "SIERRA", "3500", "V32265", "1GD49SE78NF297303"])
    sheet.append(["093", "Truck", 2009, "CHEVY", "PICKUP", "K3500", "V32265", "1GBJK74KX9E130973"])
    sheet.append(["019", "Trailer", 2014, "TITAN", "20 FT", "Trailer", "CW5105", "4TGF20204E1068483"])
    sheet.append(["032", "Trailer", 2007, "EAGER BEAVER", "Flatbed", "Trailer", "8307GC", "112SD24827L072886"])
    sheet.append(["085", "Truck", 2007, "CHEVY", "PICKUP", "K3500", "V32222", "1GBHK34K77E539288"])
    sheet.append(["091", "Truck", 2010, "PTRB", "SEMI", "DUMP", "2NJ153", "1NPTL40XXAD793867"])
    sheet.append(["095", "Truck", 2009, "PTRB", "SEMI", "388", "2NX359", "1XPWD4EX29D783855"])
    sheet.append(["098", "Truck", 2012, "KW", "SEMI", "T800", "2QV446", "1XKDD49X1CJ307144"])
    sheet.append(["101", "Truck", 2019, "PTRB", "SEMI", "389", "3CF337", "1XPXDP9XXKD489688"])
    sheet.append(["306", "Trailer", "", "OLD", "FARM", "Trailer", "", "T4821"])
    sheet.append(["030", "Truck", 2020, "TEST", "MODEL", "Truck", "TAG030", "VIN00000000000030"])
    sheet.append(["052", "Truck", 2020, "TEST", "MODEL", "Truck", "TAG052", "VIN00000000000052"])
    sheet.append(["223", "Truck", 2023, "FORD", "F-350", "Truck", "TAG223", "VIN00000000000223"])
    sheet.append(["225", "Truck", 2023, "FORD", "F-350", "Truck", "TAG225", "VIN00000000000225"])
    workbook.save(workbook_path)
    import_fleet_workbook(workbook_path, database_path)
    return database_path


def test_matches_a_unique_unit_by_vin(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(database, "VIN 1GB3KZBK9AF141680 Certificate of Registration")
    assert result["status"] == "unique"
    assert result["units"] == ["97"]
    assert result["evidence"]["97"] == ["VIN:1GB3KZBK9AF141680"]


def test_short_trailer_serial_matches_only_when_explicitly_labeled(tmp_path):
    database = _fleet_database(tmp_path)

    labeled = match_units_in_text(database, "Old trailer Serial No. T-4821")
    unlabeled = match_units_in_text(database, "Invoice 4821 includes parts and labor")

    assert labeled["status"] == "unique"
    assert labeled["units"] == ["306"]
    assert labeled["evidence"]["306"] == ["SERIAL:T4821"]
    assert unlabeled["status"] == "unmatched"


def test_flags_an_ambiguous_plate_instead_of_guessing(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(database, "Plate V32265")
    assert result["status"] == "ambiguous"
    assert result["units"] == ["93", "218"]


def test_matches_an_explicitly_labeled_unit_number(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(database, "Invoice Unit No. 097 Balance Due")
    assert result["status"] == "unique"
    assert result["units"] == ["97"]
    assert result["evidence"]["97"] == ["UNIT:97"]


def test_matches_invoice_unit_after_make_and_model_in_ocr_reading_order(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice 7/22/2026 Year Make Model Unit No 19 PETE 389 101 293651 Net 30",
    )
    assert result["status"] == "unique"
    assert result["units"] == ["101"]
    assert result["evidence"]["101"] == ["UNIT:101"]


def test_matches_invoice_unit_after_text_vehicle_description(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice 7/17/2026 Year Make Model Unit No EAGAR BEAVER 32 Labor-Mark",
    )
    assert result["status"] == "unique"
    assert result["units"] == ["32"]


def test_prefers_standalone_invoice_unit_row_over_item_prices(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Unit No\n91\nCARRY OUT ONLY\nInterstate Battery\n31 MHD\n3\n194.95\n584.85T\nParts",
    )
    assert result["status"] == "unique"
    assert result["units"] == ["91"]
    assert result["evidence"]["91"] == ["UNIT:91"]


def test_does_not_guess_unit_from_invoice_line_item_price(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Year, Make, Model Unit No 2023 F-350 Service Fee Diesel Oil Change "
        "DASH SAYS WATER IN FUEL Oil 15W40 Diesel Bulk Oil 15 52 Parts NAPA 7151",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_does_not_guess_unit_from_invoice_payment_terms(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Year, Make, Model Unit No 2235 Tire Rotation Car Wash Subtotal Total "
        "After 30 days a 1.5% interest rate will incur",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_matches_flattened_invoice_row_using_unique_database_vehicle_description(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number Mileage Rep Terms "
        "2023 FORD F-350 223 RP Net 30 extended service description and customer notes Parts",
    )

    assert result["status"] == "unique"
    assert result["units"] == ["223"]
    assert result["evidence"]["223"] == ["VEHICLE_ROW:223"]


def test_vehicle_description_fallback_does_not_treat_item_price_as_unit(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number 2023 FORD F-350 service description "
        "Diesel Oil Change Parts 52.00 Net 30",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_vehicle_description_fallback_refuses_multiple_matching_assets(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number Mileage Rep Terms "
        "2023 FORD F-350 extended notes for fleet assets 223 and 225 RP Net 30 Parts",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_vehicle_description_fallback_ignores_matching_number_far_from_vehicle_row(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number 2023 FORD F-350 "
        "service description with extensive customer complaint and repair notes "
        "Parts item extended price 223 Net 30",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_vehicle_description_fallback_rejects_generic_model_evidence(tmp_path):
    database = _fleet_database(tmp_path)
    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number 2010 PTRB SEMI 91 Net 30",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []


def test_vehicle_description_fallback_rejects_duplicate_normalized_unit_rows(tmp_path):
    database = _fleet_database(tmp_path)
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO units "
            "(source_row, display_unit, normalized_unit, unit_type, year, make, model, "
            "vehicle_type, plate, vin) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (999, "223 duplicate", "223", "Truck", "2023", "FORD", "F-350", "Truck", "", ""),
        )

    result = match_units_in_text(
        database,
        "Invoice Year Make Model Unit Number 2023 FORD F-350 223 Net 30",
    )

    assert result["status"] == "unmatched"
    assert result["units"] == []
