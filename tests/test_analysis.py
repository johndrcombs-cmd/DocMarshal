from datetime import date

import pytest

from dotdocs.analysis import classify_document, extract_controlling_date


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("ANNUAL VEHICLE INSPECTION REPORT fleet unit number 97", "DOT"),
        ("OKLAHOMA OWNERS SECURITY VERIFICATION FORM EXPIRATION DATE 02/01/2026", "INS"),
        ("SERVICE OKLAHOMA CERTIFICATE OF REGISTRATION Reg Expires 3/31/2026", "REG"),
        ("STATE OF OKLAHOMA CERTIFICATE OF TITLE Title Date 4/15/2026", "TITLE"),
        ("MANUFACTURER'S CERTIFICATE OF ORIGIN Date Issued 4/16/2026", "CERTORIGIN"),
        ("OKLAHOMA APPORTIONED CAB CARD Registration Expires 6/30/2026", "CAB"),
        ("Little B's Two, LLC Invoice Unit No 97 Balance Due", "RP"),
    ],
)
def test_classifies_supported_fleet_documents(text, expected):
    assert classify_document(text) == expected


@pytest.mark.parametrize(
    ("document_type", "text", "expected"),
    [
        ("DOT", "ANNUAL VEHICLE INSPECTION REPORT DATE 5-6-2025", date(2025, 5, 6)),
        ("RP", "Invoice Date 9/25/2025 Invoice # 24149", date(2025, 9, 25)),
        ("REG", "Reg Commence 2/11/2025 Reg Expires 3/31/2026", date(2026, 3, 31)),
        ("INS", "Effective Date 02/01/2025 Expiration Date 02/01/2026", date(2026, 2, 1)),
        ("TITLE", "Issue Date 4/15/2026", date(2026, 4, 15)),
        ("CERTORIGIN", "Date Issued 4/16/2026", date(2026, 4, 16)),
    ],
)
def test_extracts_the_document_type_controlling_date(document_type, text, expected):
    assert extract_controlling_date(text, document_type) == expected


def test_extracts_cab_card_registration_expiration():
    assert extract_controlling_date(
        "OKLAHOMA APPORTIONED CAB CARD Registration Expires 6/30/2026",
        "CAB",
    ) == date(2026, 6, 30)


def test_does_not_guess_when_the_controlling_date_is_missing():
    assert extract_controlling_date("ANNUAL VEHICLE INSPECTION REPORT", "DOT") is None


def test_uses_first_header_date_for_invoice_when_ocr_separates_labels_and_values():
    text = "Little B's Two, LLC Invoice Medford OK 7/16/2026 26146 Year Make Model Unit No 85 Parts"
    assert extract_controlling_date(text, "RP") == date(2026, 7, 16)


def test_extracts_insurance_expiration_from_ocr_table_reading_order():
    text = (
        "Policy Number Effective Date Expiration Date "
        "5069357316 02/01/2026 02/01/2027 Year Make Model"
    )

    assert extract_controlling_date(text, "INS") == date(2027, 2, 1)


def test_extracts_registration_expiration_from_ocr_vehicle_row():
    text = (
        "Service Oklahoma Certificate of Registration Trailer 1980 Timpe "
        "27U368912 7/16/2026 7/31/2027 Date Issued: July 16, 2026"
    )

    assert extract_controlling_date(text, "REG") == date(2027, 7, 31)


def test_classifies_title_from_state_form_structure_without_certificate_heading():
    text = (
        "State of Oklahoma Vehicle Identification Number Title No. Date Issued "
        "03-AUG-2026 Year Make Model Type of Title Original"
    )

    assert classify_document(text) == "TITLE"


@pytest.mark.parametrize(
    "text",
    [
        "Vehicle Identification Number TILENO. Date Issued 03-AUG-2026 Type of Title Original",
        "Vehicle Identification Number Title No. Date Issued 03-AUG-2026 Type OfTitle Original",
    ],
)
def test_classifies_title_when_ocr_removes_spaces_from_labels(text):
    assert classify_document(text) == "TITLE"


def test_extracts_title_date_with_abbreviated_month_name():
    text = "Vehicle Identification Number Title No. Date Issued 03-AUG-2026 Type of Title Original"

    assert extract_controlling_date(text, "TITLE") == date(2026, 8, 3)


def test_classifies_certificate_of_origin_from_legal_structure_before_invoice_keyword():
    text = (
        "Invoice No. 3/21/2025 Vehicle Identification No. "
        "The undersigned authorized representative hereby certify that the new vehicle "
        "is transferred to the following distributor or dealer"
    )

    assert classify_document(text) == "CERTORIGIN"


def test_does_not_classify_generic_insurance_reference_as_insurance_document():
    text = (
        "Vehicle service agreement registration title information "
        "Insurance policy number supplied for administrative reference"
    )

    assert classify_document(text) is None


def test_does_not_classify_policy_date_structure_without_insurance_form_heading():
    text = "Policy Number 123 Effective Date 02/01/2026 Expiration Date 02/01/2027"

    assert classify_document(text) is None


def test_repair_invoice_with_policy_date_references_remains_repair_document():
    text = (
        "REPAIR ORDER Policy Number 123 Effective Date 02/01/2026 "
        "Expiration Date 02/01/2027"
    )

    assert classify_document(text) == "RP"


def test_classifies_explicit_odometer_disclosure_as_misc():
    text = (
        "ODOMETER DISCLOSURE STATEMENT Federal law requires mileage disclosure "
        "Transferor signature Transferee signature"
    )

    assert classify_document(text) == "MISC"


def test_repair_invoice_referencing_odometer_disclosure_remains_repair_document():
    text = "REPAIR INVOICE customer requested odometer disclosure statement copy Parts Labor"

    assert classify_document(text) == "RP"


def test_classifies_structural_vehicle_buyers_order_as_misc():
    text = (
        "VEHICLE BUYER'S ORDER Buyer Seller Price of Vehicle Trade In "
        "Total Delivered Price Balance Due Insurance optional"
    )

    assert classify_document(text) == "MISC"


@pytest.mark.parametrize(
    "heading",
    ("VEHICLE BUYERS ORDER", "VEHICLE BUYER'S ORDER", "VEHICLE BUYER’S ORDER"),
)
def test_classifies_supported_vehicle_buyers_order_punctuation(heading):
    text = f"{heading} Price of Vehicle Total Delivered Price Invoice Certificate of Title"

    assert classify_document(text) == "MISC"


@pytest.mark.parametrize(
    "text",
    (
        "Price of Vehicle Total Delivered Price",
        "Vehicle Buyer's Order Total Delivered Price",
        "Vehicle Buyer's Order Price of Vehicle",
    ),
)
def test_vehicle_buyers_order_requires_heading_and_both_price_anchors(text):
    assert classify_document(text) is None


def test_does_not_classify_generic_vehicle_order_wording_as_misc():
    text = "Vehicle order customer price estimate and balance due"

    assert classify_document(text) is None


def test_extracts_certificate_of_origin_header_date_when_label_is_missing():
    text = (
        "Invoice No. 3/21/2025 Vehicle Identification No. "
        "The undersigned authorized representative hereby certify that the new vehicle"
    )

    assert extract_controlling_date(text, "CERTORIGIN") == date(2025, 3, 21)
