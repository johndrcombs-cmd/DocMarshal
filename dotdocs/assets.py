from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .naming import STANDARD_SUBFOLDERS
from .normalization import normalize_plate, normalize_unit, normalize_vin

ASSET_OWNERS = ("Little B's Asset", "Farm Asset")


class AssetValidationError(ValueError):
    pass


def asset_folder_root(
    asset_owner: str | None,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path | None = None,
) -> Path:
    if asset_owner == "Farm Asset":
        if farm_asset_folders_root is None:
            raise AssetValidationError("The Farm Asset binder root is not configured.")
        return Path(farm_asset_folders_root)
    return Path(unit_folders_root)


def _field(value: object, name: str, *, maximum: int = 100) -> str:
    text = str(value or "").strip()
    if len(text) > maximum:
        raise AssetValidationError(f"{name} is too long.")
    return text


def _load_registry(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AssetValidationError(f"The manual asset registry is unreadable: {error}") from error
    if not isinstance(records, list) or not all(isinstance(item, dict) for item in records):
        raise AssetValidationError("The manual asset registry is damaged; no asset was added.")
    return records


def _write_registry(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as registry_file:
            temporary_path = Path(registry_file.name)
            json.dump(records, registry_file, indent=2)
            registry_file.write("\n")
            registry_file.flush()
            os.fsync(registry_file.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _append_asset_audit(
    audit_path: str | Path,
    event: str,
    asset: dict,
    **details: object,
) -> None:
    path = Path(audit_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "unit": asset.get("unit"),
        "asset_owner": asset.get("asset_owner"),
        "plate": asset.get("plate"),
        "vin": asset.get("vin"),
        **details,
    }
    with path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record, sort_keys=True) + "\n")
        audit_file.flush()
        os.fsync(audit_file.fileno())


def _ensure_asset_columns(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(units)")}
    if "asset_owner" not in columns:
        connection.execute("ALTER TABLE units ADD COLUMN asset_owner TEXT")
    if "asset_source" not in columns:
        connection.execute("ALTER TABLE units ADD COLUMN asset_source TEXT NOT NULL DEFAULT 'workbook'")
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_unit_unique "
        "ON units(normalized_unit) WHERE asset_source = 'manual' AND normalized_unit <> ''"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_plate_unique "
        "ON units(plate) WHERE asset_source = 'manual' AND plate <> ''"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_vin_unique "
        "ON units(vin) WHERE asset_source = 'manual' AND vin <> ''"
    )


def _validate_asset(
    *,
    unit: object,
    asset_owner: object,
    unit_type: object,
    year: object,
    make: object,
    model: object,
    vehicle_type: object,
    plate: object,
    vin: object,
    fuel_type: object,
) -> dict:
    unit_text = _field(unit, "Unit number", maximum=12)
    if not re.fullmatch(r"\d+", unit_text):
        raise AssetValidationError("Enter a unit number using digits only.")
    normalized_unit = normalize_unit(unit_text)
    owner = _field(asset_owner, "Asset owner", maximum=40)
    if owner not in ASSET_OWNERS:
        raise AssetValidationError("Choose Little B's Asset or Farm Asset.")
    normalized_plate = normalize_plate(plate)
    normalized_vin = normalize_vin(vin)
    if not normalized_plate and not normalized_vin:
        raise AssetValidationError(
            "Enter at least a VIN/serial number or license plate/tag so the asset can be identified later."
        )
    if normalized_plate and len(normalized_plate) < 4:
        raise AssetValidationError("The license plate/tag must contain at least 4 letters or numbers.")
    if normalized_vin and len(normalized_vin) < 4:
        raise AssetValidationError(
            "The VIN/serial number must contain at least 4 letters or numbers."
        )
    year_text = _field(year, "Year", maximum=4)
    if year_text and not re.fullmatch(r"\d{4}", year_text):
        raise AssetValidationError("Enter the year using four digits.")
    return {
        "unit": normalized_unit,
        "asset_owner": owner,
        "unit_type": _field(unit_type, "Unit type"),
        "year": year_text,
        "make": _field(make, "Make"),
        "model": _field(model, "Model"),
        "vehicle_type": _field(vehicle_type, "Vehicle type"),
        "plate": normalized_plate,
        "vin": normalized_vin,
        "fuel_type": _field(fuel_type, "Fuel type"),
    }


def register_manual_asset(
    *,
    database_path: str | Path,
    registry_path: str | Path,
    audit_path: str | Path,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path | None = None,
    unit: object,
    asset_owner: object,
    unit_type: object = "",
    year: object = "",
    make: object = "",
    model: object = "",
    vehicle_type: object = "",
    plate: object = "",
    vin: object = "",
    fuel_type: object = "",
) -> dict:
    asset = _validate_asset(
        unit=unit,
        asset_owner=asset_owner,
        unit_type=unit_type,
        year=year,
        make=make,
        model=model,
        vehicle_type=vehicle_type,
        plate=plate,
        vin=vin,
        fuel_type=fuel_type,
    )
    database_path = Path(database_path)
    registry_path = Path(registry_path)
    unit_folders_root = Path(unit_folders_root)
    if not database_path.is_file():
        raise AssetValidationError(f"The fleet database is unavailable: {database_path}")
    selected_root = asset_folder_root(asset["asset_owner"], unit_folders_root, farm_asset_folders_root)
    if not selected_root.is_dir():
        raise AssetValidationError(f"The production unit-folder root is unavailable: {selected_root}")

    _append_asset_audit(audit_path, "asset_creation_started", asset)
    unit_folder = selected_root / f"Unit_{asset['unit']}"
    staging_folder = selected_root / f".dotdocs-Unit_{asset['unit']}-{uuid.uuid4().hex}.tmp"
    existing_registry: list[dict] = []
    registry_written = False
    folder_published = False
    compensation_errors: list[str] = []

    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            _ensure_asset_columns(connection)
            existing_registry = _load_registry(registry_path)
            for saved_record in existing_registry:
                try:
                    saved_asset = _validate_asset(**saved_record)
                except (TypeError, AssetValidationError) as error:
                    raise AssetValidationError(
                        f"The manual asset registry contains an invalid record: {error}"
                    ) from error
                if saved_asset["unit"] == asset["unit"]:
                    raise AssetValidationError(f"Unit {asset['unit']} already exists in the manual asset registry.")
                if asset["vin"] and saved_asset["vin"] == asset["vin"]:
                    raise AssetValidationError(
                        "That VIN/serial number is already assigned in the manual asset registry."
                    )
                if asset["plate"] and saved_asset["plate"] == asset["plate"]:
                    raise AssetValidationError("That license plate/tag is already assigned in the manual asset registry.")

            conflicts = connection.execute(
                """
                SELECT normalized_unit, plate, vin FROM units
                WHERE normalized_unit = ?
                   OR (? <> '' AND plate = ?)
                   OR (? <> '' AND vin = ?)
                """,
                (
                    asset["unit"],
                    asset["plate"], asset["plate"],
                    asset["vin"], asset["vin"],
                ),
            ).fetchall()
            if conflicts:
                if any(row[0] == asset["unit"] for row in conflicts):
                    raise AssetValidationError(f"Unit {asset['unit']} already exists in the fleet database.")
                if asset["vin"] and any(row[2] == asset["vin"] for row in conflicts):
                    raise AssetValidationError(
                        "That VIN/serial number is already assigned to another unit."
                    )
                raise AssetValidationError("That license plate/tag is already assigned to another unit.")
            if unit_folder.exists():
                raise AssetValidationError(f"The canonical production folder already exists: {unit_folder}")

            staging_folder.mkdir()
            for subfolder in STANDARD_SUBFOLDERS:
                (staging_folder / subfolder).mkdir()

            _write_registry(registry_path, [*existing_registry, asset])
            registry_written = True
            connection.execute(
                """
                INSERT INTO units (
                    source_row, display_unit, normalized_unit, unit_type, year, make,
                    model, vehicle_type, plate, vin, fuel_type, next_dot, dot_status,
                    asset_owner, asset_source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    0, asset["unit"], asset["unit"], asset["unit_type"], asset["year"],
                    asset["make"], asset["model"], asset["vehicle_type"], asset["plate"],
                    asset["vin"], asset["fuel_type"], "", "", asset["asset_owner"], "manual",
                ),
            )
            staging_folder.rename(unit_folder)
            folder_published = True
            _append_asset_audit(audit_path, "asset_created", asset)
            connection.commit()
        except Exception as error:
            if registry_written:
                try:
                    _write_registry(registry_path, existing_registry)
                except Exception as compensation_error:
                    compensation_errors.append(f"registry rollback failed: {compensation_error}")
            try:
                if folder_published and unit_folder.exists():
                    shutil.rmtree(unit_folder)
                elif staging_folder.exists():
                    shutil.rmtree(staging_folder)
            except Exception as compensation_error:
                compensation_errors.append(f"folder rollback failed: {compensation_error}")
            try:
                _append_asset_audit(
                    audit_path,
                    "asset_creation_failed",
                    asset,
                    reason=str(error),
                    compensation_errors=compensation_errors,
                )
            except Exception:
                pass
            try:
                connection.rollback()
            except Exception as compensation_error:
                compensation_errors.append(f"database rollback failed: {compensation_error}")
            if compensation_errors:
                raise AssetValidationError(
                    f"Asset creation failed and rollback was incomplete: {'; '.join(compensation_errors)}"
                ) from error
            raise
    return asset


def ensure_standard_unit_folder(
    *,
    database_path: str | Path,
    audit_path: str | Path,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path | None,
    unit: object,
) -> tuple[Path, str | None]:
    normalized_unit = normalize_unit(unit)
    if not normalized_unit:
        raise AssetValidationError("A valid fleet unit number is required before folders can be created.")
    database_path = Path(database_path)
    if not database_path.is_file():
        raise AssetValidationError(f"The fleet database is unavailable: {database_path}")

    created_paths: list[Path] = []
    staging_folder: Path | None = None
    published_folder: Path | None = None
    with closing(sqlite3.connect(database_path, timeout=30)) as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT asset_owner FROM units WHERE normalized_unit = ?",
                (normalized_unit,),
            ).fetchall()
            if len(rows) != 1:
                if not rows:
                    raise AssetValidationError(
                        f"Unit {normalized_unit} is not uniquely verified in the fleet database; no folders were created."
                    )
                raise AssetValidationError(
                    f"Unit {normalized_unit} has duplicate fleet records; no folders were created."
                )
            asset_owner = rows[0][0] or None
            if asset_owner is not None and asset_owner not in ASSET_OWNERS:
                raise AssetValidationError(
                    f"Unit {normalized_unit} has an invalid asset ownership value; no folders were created."
                )
            selected_root = asset_folder_root(
                asset_owner, unit_folders_root, farm_asset_folders_root
            )
            if not selected_root.is_dir():
                raise AssetValidationError(
                    f"The production unit-folder root is unavailable: {selected_root}"
                )
            unit_folder = selected_root / f"Unit_{normalized_unit}"
            if unit_folder.exists():
                if not unit_folder.is_dir() or unit_folder.resolve().parent != selected_root.resolve():
                    raise AssetValidationError(
                        f"The canonical production folder is unsafe or invalid: {unit_folder}"
                    )
                missing_subfolders = [
                    unit_folder / name
                    for name in STANDARD_SUBFOLDERS
                    if not (unit_folder / name).is_dir()
                ]
                if not missing_subfolders:
                    connection.commit()
                    return unit_folder, asset_owner
            else:
                missing_subfolders = []

            audit_asset = {
                "unit": normalized_unit,
                "asset_owner": asset_owner,
                "plate": "",
                "vin": "",
            }
            _append_asset_audit(
                audit_path,
                "unit_folder_creation_started",
                audit_asset,
                destination=str(unit_folder),
            )
            if unit_folder.exists():
                for subfolder in missing_subfolders:
                    subfolder.mkdir()
                    created_paths.append(subfolder)
            else:
                staging_folder = selected_root / (
                    f".dotdocs-Unit_{normalized_unit}-{uuid.uuid4().hex}.tmp"
                )
                staging_folder.mkdir()
                for subfolder in STANDARD_SUBFOLDERS:
                    (staging_folder / subfolder).mkdir()
                staging_folder.rename(unit_folder)
                published_folder = unit_folder
            _append_asset_audit(
                audit_path,
                "unit_folder_created",
                audit_asset,
                destination=str(unit_folder),
            )
            connection.commit()
            return unit_folder, asset_owner
        except Exception as error:
            try:
                if published_folder is not None and published_folder.exists():
                    shutil.rmtree(published_folder)
                elif staging_folder is not None and staging_folder.exists():
                    shutil.rmtree(staging_folder)
                else:
                    for created_path in reversed(created_paths):
                        created_path.rmdir()
            finally:
                connection.rollback()
            if isinstance(error, AssetValidationError):
                raise
            raise AssetValidationError(
                f"The standard folders for unit {normalized_unit} could not be created: {error}"
            ) from error


def load_manual_assets(registry_path: str | Path) -> list[dict]:
    return _load_registry(Path(registry_path))
