from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from .normalization import normalize_plate, normalize_unit, normalize_vin


def _sort_units(units):
    return sorted(units, key=lambda value: (len(value), value))


GENERIC_MODELS = {
    "DUMP",
    "FARM",
    "FLATBED",
    "MODEL",
    "PICKUP",
    "SEMI",
    "TRAILER",
    "TRUCK",
}


def _description_pattern(value: object) -> str | None:
    words = re.findall(r"[A-Z0-9]+", str(value or "").upper())
    normalized = " ".join(words)
    if not words or normalized in GENERIC_MODELS:
        return None
    return r"(?<![A-Z0-9])" + r"[^A-Z0-9]{0,3}".join(
        re.escape(word) for word in words
    ) + r"(?![A-Z0-9])"


def match_units_in_text(database_path: str | Path, text: str) -> dict:
    raw_text = str(text or "")
    compact_text = re.sub(r"[^A-Z0-9]", "", raw_text.upper())
    labeled_identifiers = {
        normalize_vin(match.group(1))
        for match in re.finditer(
            r"\b(?:VIN|SERIAL(?:\s*(?:NO\.?|NUMBER|#))?|S\s*/\s*N)"
            r"\s*[:#-]?\s*([A-Z0-9]+(?:[- ]?[A-Z0-9]+)?)",
            raw_text,
            flags=re.IGNORECASE,
        )
    }
    evidence: dict[str, set[str]] = defaultdict(set)

    with closing(sqlite3.connect(database_path)) as connection:
        records = connection.execute(
            "SELECT normalized_unit, plate, vin, year, make, model FROM units WHERE normalized_unit <> ''"
        ).fetchall()

    known_units = {record[0] for record in records}
    unit_record_counts = Counter(record[0] for record in records)

    for unit, plate, vin, _year, _make, _model in records:
        normalized_vin = normalize_vin(vin)
        if normalized_vin and len(normalized_vin) >= 11 and normalized_vin in compact_text:
            evidence[unit].add(f"VIN:{vin}")
        elif normalized_vin and len(normalized_vin) >= 4 and normalized_vin in labeled_identifiers:
            evidence[unit].add(f"SERIAL:{normalized_vin}")
        if plate and len(plate) >= 4 and normalize_plate(plate) in compact_text:
            evidence[unit].add(f"PLATE:{plate}")

    invoice_header_unit = None
    unit_label = re.search(r"\bUNIT\s*NO\.?\b", raw_text, flags=re.IGNORECASE)
    if unit_label:
        unit_section = raw_text[unit_label.end() : unit_label.end() + 300]
        unit_section = re.split(
            r"\b(?:NET\s*30|TERMS|PARTS|LABOR|ITEM|DESCRIPTION)\b",
            unit_section,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0]
        line_candidates = []
        for line in (line.strip() for line in unit_section.splitlines()[:12]):
            match = re.fullmatch(r"0*(\d{1,4})", line)
            if match:
                normalized = normalize_unit(match.group(1))
                if normalized in known_units:
                    line_candidates.append(normalized)
        if line_candidates:
            invoice_header_unit = line_candidates[0]
        else:
            fallback_section = re.sub(
                r"\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})",
                " ",
                unit_section[:40],
            )
            candidates = []
            for token in re.findall(
                r"(?<![A-Za-z0-9.])0*(\d{1,4})(?![A-Za-z0-9.])",
                fallback_section,
            ):
                normalized = normalize_unit(token)
                if normalized in known_units:
                    candidates.append(normalized)
            if candidates:
                invoice_header_unit = candidates[-1]
        if invoice_header_unit:
            evidence[invoice_header_unit].add(f"UNIT:{invoice_header_unit}")

    unit_patterns = (
        r"\bUNIT\s*(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*0*(\d{1,4})\b",
        r"\bFLEET\s+UNIT\s+(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*0*(\d{1,4})\b",
    )
    if invoice_header_unit is None:
        for pattern in unit_patterns:
            for match in re.finditer(pattern, raw_text, flags=re.IGNORECASE):
                unit = normalize_unit(match.group(1))
                if unit in known_units:
                    evidence[unit].add(f"UNIT:{unit}")

    if not evidence and re.search(r"\b(?:INVOICE|REPAIR ORDER|WORK ORDER)\b", raw_text, flags=re.IGNORECASE):
        has_unit_column = re.search(
            r"\bUNIT\s*(?:NO\.?|NUMBER|#)\b",
            raw_text,
            flags=re.IGNORECASE,
        )
        vehicle_row_candidates = set()
        if has_unit_column:
            for unit, _plate, _vin, year, make, model in records:
                if unit_record_counts[unit] != 1:
                    continue
                model_pattern = _description_pattern(model)
                if not model_pattern:
                    continue
                make_pattern = _description_pattern(make)
                unit_pattern = (
                    rf"(?<![A-Z0-9.])0*{re.escape(unit)}(?![A-Z0-9.])"
                )
                for model_match in re.finditer(
                    model_pattern,
                    raw_text,
                    flags=re.IGNORECASE,
                ):
                    row_prefix = raw_text[max(0, model_match.start() - 80) : model_match.start()]
                    row_suffix = raw_text[model_match.end() : model_match.end() + 50]
                    year_present = bool(
                        year and re.search(rf"\b{re.escape(str(year))}\b", row_prefix)
                    )
                    make_present = bool(
                        make_pattern
                        and re.search(make_pattern, row_prefix, flags=re.IGNORECASE)
                    )
                    if (year_present or make_present) and re.search(
                        unit_pattern,
                        row_suffix,
                        flags=re.IGNORECASE,
                    ):
                        vehicle_row_candidates.add(unit)
                        break
        if len(vehicle_row_candidates) == 1:
            unit = next(iter(vehicle_row_candidates))
            evidence[unit].add(f"VEHICLE_ROW:{unit}")

    units = _sort_units(evidence)
    if not units:
        status = "unmatched"
    elif len(units) == 1:
        status = "unique"
    else:
        status = "ambiguous"

    return {
        "status": status,
        "units": units,
        "evidence": {unit: sorted(evidence[unit]) for unit in units},
    }
