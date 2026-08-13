from __future__ import annotations

import re
from datetime import date, datetime


CLASSIFICATION_RULES = (
    ("CAL", ("CERTIFICATE OF CALIBRATION", "CALIBRATION CERTIFICATE")),
    ("DOT", ("ANNUAL VEHICLE INSPECTION",)),
    ("INS", ("SECURITY VERIFICATION FORM",)),
    ("CAB", ("APPORTIONED CAB CARD", "CAB CARD")),
    ("REG", ("CERTIFICATE OF REGISTRATION", "REGISTRATION CERTIFICATE")),
    ("TITLE", ("CERTIFICATE OF TITLE", "VEHICLE TITLE")),
    (
        "CERTORIGIN",
        (
            "CERTIFICATE OF ORIGIN",
            "MANUFACTURER'S STATEMENT OF ORIGIN",
            "MANUFACTURERS STATEMENT OF ORIGIN",
        ),
    ),
    ("RP", ("INVOICE", "REPAIR ORDER", "WORK ORDER")),
)

NUMERIC_DATE_VALUE = r"\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})"
TEXTUAL_DATE_VALUE = r"\d{1,2}-[A-Z]{3,9}-\d{4}"
DATE_TOKEN = rf"({NUMERIC_DATE_VALUE})\b"
DATE_VALUE_TOKEN = rf"({NUMERIC_DATE_VALUE}|{TEXTUAL_DATE_VALUE})\b"
DATE_LABELS = {
    "DOT": (r"\bDATE\b", r"INSPECTION\s+DATE"),
    "RP": (r"INVOICE\s+DATE", r"SERVICE\s+DATE", r"\bDATE\b"),
    "REG": (r"REG(?:ISTRATION)?\s+EXPIRES", r"EXPIRATION\s+DATE"),
    "CAB": (r"REG(?:ISTRATION)?\s+EXPIRES", r"EXPIRATION\s+DATE", r"EXPIRES"),
    "INS": (r"EXPIRATION\s+DATE", r"POLICY\s+EXPIRES"),
    "TITLE": (r"TITLE\s+DATE", r"ISSUE\s+DATE", r"DATE\s+ISSUED"),
    "CERTORIGIN": (r"DATE\s+ISSUED", r"ISSUE\s+DATE"),
    "CAL": (r"DUE\s+DATE", r"CALIBRATION\s+DUE", r"EXPIRATION\s+DATE", r"EXPIRES"),
}


def _collapsed(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip().upper()


def classify_document(text: str) -> str | None:
    normalized = _collapsed(text)
    if all(
        keyword in normalized
        for keyword in (
            "ODOMETER DISCLOSURE STATEMENT",
            "TRANSFEROR",
            "TRANSFEREE",
        )
    ):
        return "MISC"
    if (
        re.search(r"\bVEHICLE\s+BUYER\W*S\s+ORDER\b", normalized)
        and "PRICE OF VEHICLE" in normalized
        and "TOTAL DELIVERED PRICE" in normalized
    ):
        return "MISC"
    if (
        "VEHICLE IDENTIFICATION" in normalized
        and re.search(r"\bTIT?LE\s*NO\b", normalized)
        and re.search(r"\bTYPE\s+OF\s*TITLE\b", normalized)
    ):
        return "TITLE"
    if all(
        keyword in normalized
        for keyword in (
            "UNDERSIGNED AUTHORIZED REPRESENTATIVE",
            "HEREBY CERTIFY",
            "NEW VEHICLE",
        )
    ):
        return "CERTORIGIN"
    for document_type, keywords in CLASSIFICATION_RULES:
        if any(keyword in normalized for keyword in keywords):
            return document_type
    return None


def _parse_date(value: str) -> date | None:
    if re.search(r"[A-Z]", value, flags=re.IGNORECASE):
        for date_format in ("%d-%b-%Y", "%d-%B-%Y"):
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                pass
        return None
    parts = [int(part) for part in re.split(r"[/-]", value)]
    month, day, year = parts
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def extract_controlling_date(text: str, document_type: str) -> date | None:
    normalized_type = str(document_type or "").upper()
    normalized_text = _collapsed(text)
    for label in DATE_LABELS.get(normalized_type, ()):
        match = re.search(label + r"[^0-9]{0,40}" + DATE_VALUE_TOKEN, normalized_text)
        if match:
            parsed = _parse_date(match.group(1))
            if parsed:
                return parsed
    if normalized_type == "INS":
        table = re.search(
            r"EFFECTIVE\s+DATE\s+EXPIRATION\s+DATE(.{0,160})",
            normalized_text,
        )
        if table:
            values = re.findall(DATE_TOKEN, table.group(1))
            if len(values) >= 2:
                return _parse_date(values[1])
    if normalized_type in {"REG", "CAB"}:
        vehicle_row = re.search(DATE_TOKEN + r"\s+" + DATE_TOKEN, normalized_text)
        if vehicle_row:
            return _parse_date(vehicle_row.group(2))
    if normalized_type == "CERTORIGIN":
        header_match = re.search(DATE_TOKEN, normalized_text[:250])
        if header_match:
            return _parse_date(header_match.group(1))
    if normalized_type == "RP":
        header_match = re.search(DATE_TOKEN, normalized_text[:500])
        if header_match:
            return _parse_date(header_match.group(1))
    return None
