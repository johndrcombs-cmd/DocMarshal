from __future__ import annotations

import sqlite3
import os
import tempfile
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from .normalization import normalize_plate, normalize_unit, normalize_vin

SCHEMA = """
CREATE TABLE units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_row INTEGER NOT NULL,
    display_unit TEXT NOT NULL,
    normalized_unit TEXT NOT NULL,
    unit_type TEXT,
    year TEXT,
    make TEXT,
    model TEXT,
    vehicle_type TEXT,
    plate TEXT,
    vin TEXT,
    fuel_type TEXT,
    next_dot TEXT,
    dot_status TEXT,
    asset_owner TEXT,
    asset_source TEXT NOT NULL DEFAULT 'workbook'
);
CREATE INDEX idx_units_normalized_unit ON units(normalized_unit);
CREATE INDEX idx_units_plate ON units(plate);
CREATE INDEX idx_units_vin ON units(vin);
CREATE UNIQUE INDEX idx_manual_unit_unique ON units(normalized_unit)
    WHERE asset_source = 'manual' AND normalized_unit <> '';
CREATE UNIQUE INDEX idx_manual_plate_unique ON units(plate)
    WHERE asset_source = 'manual' AND plate <> '';
CREATE UNIQUE INDEX idx_manual_vin_unique ON units(vin)
    WHERE asset_source = 'manual' AND vin <> '';

CREATE TABLE import_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_row INTEGER,
    issue_type TEXT NOT NULL,
    identifier_value TEXT,
    details TEXT
);
"""


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _create_database(database_path: Path) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.executescript(SCHEMA)
    return connection


def import_fleet_workbook(
    workbook_path: str | Path,
    database_path: str | Path,
    *,
    manual_assets_path: str | Path | None = None,
) -> dict[str, Any]:
    workbook_path = Path(workbook_path)
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = tempfile.NamedTemporaryFile(
        dir=database_path.parent,
        prefix=database_path.name + ".",
        suffix=".tmp",
        delete=False,
    )
    temporary_database = Path(temporary_file.name)
    temporary_file.close()
    temporary_database.unlink()
    workbook = load_workbook(workbook_path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    headers = [_text(cell.value) for cell in sheet[1]]

    imported = []
    manual_count = 0
    try:
        with closing(_create_database(temporary_database)) as connection:
            for source_row, values in enumerate(sheet.iter_rows(min_row=2, values_only=True), start=2):
                record = dict(zip(headers, values))
                display_unit = _text(record.get("Unit #"))
                normalized_unit = normalize_unit(display_unit)
                plate = normalize_plate(record.get("Tag"))
                vin = normalize_vin(record.get("Vin"))
                connection.execute(
                    """
                    INSERT INTO units (
                        source_row, display_unit, normalized_unit, unit_type, year, make,
                        model, vehicle_type, plate, vin, fuel_type, next_dot, dot_status,
                        asset_owner, asset_source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_row, display_unit, normalized_unit, _text(record.get("Unit Type")),
                        _text(record.get("Year")), _text(record.get("Make")), _text(record.get("Model")),
                        _text(record.get("Type")), plate, vin, _text(record.get("Fuel Type")),
                        _text(record.get("Next DOT")), _text(record.get("DOT Status")),
                        _text(record.get("Asset Owner")), "workbook",
                    ),
                )
                imported.append({"source_row": source_row, "unit": normalized_unit, "plate": plate, "vin": vin})

            if manual_assets_path is not None:
                from .assets import _validate_asset, load_manual_assets

                for saved_asset in load_manual_assets(manual_assets_path):
                    asset = _validate_asset(**saved_asset)
                    conflict = connection.execute(
                        """SELECT normalized_unit, plate, vin FROM units
                           WHERE normalized_unit = ? OR (? <> '' AND plate = ?) OR (? <> '' AND vin = ?)""",
                        (asset["unit"], asset["plate"], asset["plate"], asset["vin"], asset["vin"]),
                    ).fetchone()
                    if conflict:
                        raise ValueError(f"Manual asset {asset['unit']} conflicts with existing fleet data.")
                    connection.execute(
                        """INSERT INTO units (
                            source_row, display_unit, normalized_unit, unit_type, year, make,
                            model, vehicle_type, plate, vin, fuel_type, next_dot, dot_status,
                            asset_owner, asset_source
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (0, asset["unit"], asset["unit"], asset["unit_type"], asset["year"],
                         asset["make"], asset["model"], asset["vehicle_type"], asset["plate"],
                         asset["vin"], asset["fuel_type"], "", "", asset["asset_owner"], "manual"),
                    )
                    imported.append({"source_row": 0, "unit": asset["unit"], "plate": asset["plate"], "vin": asset["vin"]})
                    manual_count += 1

            duplicate_fields = {"plate": "AMBIGUOUS_PLATE", "vin": "AMBIGUOUS_VIN", "unit": "DUPLICATE_UNIT"}
            ambiguities: dict[str, dict[str, list[str]]] = {}
            for field, issue_type in duplicate_fields.items():
                grouped: dict[str, list[str]] = defaultdict(list)
                for record in imported:
                    identifier = record[field]
                    if identifier:
                        grouped[identifier].append(record["unit"])
                duplicate_values = {
                    identifier: sorted(set(units), key=lambda value: (len(value), value))
                    for identifier, units in grouped.items()
                    if len(units) > 1
                }
                ambiguities[field] = duplicate_values
                for identifier, units in duplicate_values.items():
                    connection.execute(
                        "INSERT INTO import_issues(issue_type, identifier_value, details) VALUES (?, ?, ?)",
                        (issue_type, identifier, ",".join(units)),
                    )
            connection.commit()
        os.replace(temporary_database, database_path)
    finally:
        workbook.close()
        temporary_database.unlink(missing_ok=True)
    return {
        "records_imported": len(imported),
        "manual_records_imported": manual_count,
        "ambiguous_plates": ambiguities["plate"],
        "ambiguous_vins": ambiguities["vin"],
        "duplicate_units": ambiguities["unit"],
    }


def find_units_by_identifier(
    database_path: str | Path,
    *,
    unit: Any = None,
    vin: Any = None,
    plate: Any = None,
) -> list[str]:
    clauses = []
    parameters = []
    if unit is not None:
        clauses.append("normalized_unit = ?")
        parameters.append(normalize_unit(unit))
    if vin is not None:
        clauses.append("vin = ?")
        parameters.append(normalize_vin(vin))
    if plate is not None:
        clauses.append("plate = ?")
        parameters.append(normalize_plate(plate))
    if not clauses:
        return []

    query = "SELECT DISTINCT normalized_unit FROM units WHERE " + " OR ".join(clauses)
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return sorted((row[0] for row in rows if row[0]), key=lambda value: (len(value), value))


def find_asset_owner(database_path: str | Path, unit: Any) -> str | None:
    normalized_unit = normalize_unit(unit)
    if not normalized_unit:
        return None
    with closing(sqlite3.connect(database_path)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(units)")}
        if "asset_owner" not in columns:
            return None
        rows = connection.execute(
            "SELECT DISTINCT asset_owner FROM units WHERE normalized_unit = ? AND asset_owner <> ''",
            (normalized_unit,),
        ).fetchall()
    owners = {row[0] for row in rows if row[0]}
    return next(iter(owners)) if len(owners) == 1 else None
