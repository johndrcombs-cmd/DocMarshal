from datetime import date

import pytest

from dotdocs.naming import build_filename, destination_subfolder


@pytest.mark.parametrize(
    ("document_type", "controlling_date", "expected"),
    [
        ("DOT", date(2025, 5, 6), "97_DOT_05-06-2025.pdf"),
        ("RP", date(2025, 9, 25), "97_RP_09-25-2025.pdf"),
        ("REG", date(2026, 3, 31), "97_REG_03-31-2026.pdf"),
        ("TITLE", date(2026, 4, 15), "97_TITLE_04-15-2026.pdf"),
        ("CERTORIGIN", date(2026, 4, 16), "97_CERTORIGIN_04-16-2026.pdf"),
        ("CAB", date(2026, 6, 30), "97_CAB_06-30-2026.pdf"),
        ("INS", date(2026, 2, 1), "97_INS_02-01-2026.pdf"),
        ("MISC", date(2026, 8, 11), "97_MISC_08-11-2026.pdf"),
    ],
)
def test_builds_authoritative_filenames(document_type, controlling_date, expected):
    assert build_filename("097", document_type, controlling_date) == expected


def test_adds_meaningful_suffix_without_overwriting():
    assert build_filename("97", "RP", date(2025, 9, 25), suffix="Invoice 24149") == (
        "97_RP_09-25-2025_Invoice-24149.pdf"
    )


@pytest.mark.parametrize(
    ("document_type", "expected"),
    [
        ("DOT", "001_Annual_DOT"),
        ("INS", "002_Insurance"),
        ("REG", "003_Registration"),
        ("TITLE", "003_Registration"),
        ("CERTORIGIN", "003_Registration"),
        ("CAB", "003_Registration"),
        ("RP", "004_Maintenance_Records"),
        ("MISC", "005_Misc"),
    ],
)
def test_maps_document_types_to_existing_subfolders(document_type, expected):
    assert destination_subfolder(document_type) == expected
