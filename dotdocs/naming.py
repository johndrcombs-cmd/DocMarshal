from __future__ import annotations

import re
from datetime import date

from .normalization import normalize_unit

DESTINATION_SUBFOLDERS = {
    "DOT": "001_Annual_DOT",
    "INS": "002_Insurance",
    "REG": "003_Registration",
    "TITLE": "003_Registration",
    "CERTORIGIN": "003_Registration",
    "RP": "004_Maintenance_Records",
}

DOCUMENT_TYPE_CHOICES = ("DOT", "RP", "REG", "TITLE", "CERTORIGIN", "INS")
STANDARD_SUBFOLDERS = tuple(dict.fromkeys(DESTINATION_SUBFOLDERS.values()))


def _normalize_document_type(document_type: str) -> str:
    normalized = str(document_type).strip().upper()
    if normalized not in DESTINATION_SUBFOLDERS:
        raise ValueError(f"Unsupported document type: {document_type}")
    return normalized


def _safe_suffix(suffix: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", suffix.strip())
    return cleaned.strip("-")


def build_filename(
    unit: str,
    document_type: str,
    controlling_date: date,
    *,
    suffix: str | None = None,
) -> str:
    normalized_unit = normalize_unit(unit)
    if not normalized_unit:
        raise ValueError("A unit number is required")
    normalized_type = _normalize_document_type(document_type)
    stem = f"{normalized_unit}_{normalized_type}_{controlling_date:%m-%d-%Y}"
    if suffix:
        safe_suffix = _safe_suffix(suffix)
        if safe_suffix:
            stem += f"_{safe_suffix}"
    return stem + ".pdf"


def destination_subfolder(document_type: str) -> str:
    return DESTINATION_SUBFOLDERS[_normalize_document_type(document_type)]
