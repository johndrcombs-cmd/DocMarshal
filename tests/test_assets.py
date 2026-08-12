import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from openpyxl import Workbook

import dotdocs.assets as assets
from dotdocs.assets import AssetValidationError, register_manual_asset
from dotdocs.database import find_asset_owner, import_fleet_workbook
from dotdocs.matching import match_units_in_text


def _workbook(tmp_path):
    workbook_path = tmp_path / "fleet.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Unit #", "Unit Type", "Year", "Make", "Model", "Type", "Tag", "Vin"])
    sheet.append(["91", "Truck", "2018", "Ford", "F-350", "Truck", "OLD91", "1FT8W3BT0JEC00091"])
    workbook.save(workbook_path)
    return workbook_path


def _database(tmp_path):
    workbook_path = _workbook(tmp_path)
    database_path = tmp_path / "fleet.db"
    import_fleet_workbook(workbook_path, database_path)
    return database_path


def test_register_manual_asset_persists_owner_database_and_standard_folders(tmp_path):
    database_path = _database(tmp_path)
    registry_path = tmp_path / "manual_assets.json"
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    farm_root = tmp_path / "Farm Assets"
    farm_root.mkdir()

    asset = register_manual_asset(
        database_path=database_path,
        registry_path=registry_path,
        audit_path=tmp_path / "audit.jsonl",
        unit_folders_root=unit_root,
        farm_asset_folders_root=farm_root,
        unit="305",
        asset_owner="Farm Asset",
        unit_type="Tractor",
        year="2024",
        make="John Deere",
        model="5075E",
        vehicle_type="Farm Equipment",
        plate="FARM305",
        vin="1LV5075EVRR123456",
        fuel_type="Diesel",
    )

    assert asset["unit"] == "305"
    assert asset["asset_owner"] == "Farm Asset"
    assert json.loads(registry_path.read_text(encoding="utf-8")) == [asset]
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT normalized_unit, asset_owner, asset_source, plate, vin FROM units WHERE normalized_unit = ?",
            ("305",),
        ).fetchone()
    assert row == ("305", "Farm Asset", "manual", "FARM305", "1LV5075EVRR123456")
    assert not (unit_root / "Unit_305").exists()
    assert sorted(path.name for path in (farm_root / "Unit_305").iterdir()) == [
        "001_Annual_DOT",
        "002_Insurance",
        "003_Registration",
        "004_Maintenance_Records",
        "005_Misc",
    ]
    audit_records = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["event"] for record in audit_records] == [
        "asset_creation_started",
        "asset_created",
    ]
    assert audit_records[-1]["asset_owner"] == "Farm Asset"


def test_register_old_trailer_accepts_short_serial_number_as_identifier(tmp_path):
    database_path = _database(tmp_path)
    registry_path = tmp_path / "manual_assets.json"
    unit_root = tmp_path / "DOT Binders"
    unit_root.mkdir()
    farm_root = tmp_path / "Farm Assets"
    farm_root.mkdir()

    asset = register_manual_asset(
        database_path=database_path,
        registry_path=registry_path,
        audit_path=tmp_path / "audit.jsonl",
        unit_folders_root=unit_root,
        farm_asset_folders_root=farm_root,
        unit="306",
        asset_owner="Farm Asset",
        unit_type="Trailer",
        vin="T-4821",
    )

    assert asset["vin"] == "T4821"
    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT normalized_unit, vin FROM units WHERE normalized_unit = '306'"
        ).fetchone()
    assert row == ("306", "T4821")
    assert (farm_root / "Unit_306").is_dir()
    assert not (unit_root / "Unit_306").exists()


@pytest.mark.parametrize(
    ("unit", "plate", "vin", "message"),
    [
        ("91", "NEWPLATE", "1LV5075EVRR123456", "Unit 91 already exists"),
        ("305", "OLD91", "1LV5075EVRR123456", "plate/tag is already assigned"),
        ("305", "NEWPLATE", "1FT8W3BT0JEC00091", "VIN/serial number is already assigned"),
    ],
)
def test_register_manual_asset_rejects_duplicate_identifiers(tmp_path, unit, plate, vin, message):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()

    with pytest.raises(AssetValidationError, match=message):
        register_manual_asset(
            database_path=database_path,
            registry_path=tmp_path / "manual_assets.json",
            audit_path=tmp_path / "audit.jsonl",
            unit_folders_root=unit_root,
            unit=unit,
            asset_owner="Little B's Asset",
            plate=plate,
            vin=vin,
        )

    assert not (tmp_path / "manual_assets.json").exists()
    assert not (unit_root / f"Unit_{unit}").exists()


def test_register_manual_asset_rejects_duplicate_short_serial(tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "DOT Binders"
    unit_root.mkdir()
    registry_path = tmp_path / "manual_assets.json"
    audit_path = tmp_path / "audit.jsonl"
    common = {
        "database_path": database_path,
        "registry_path": registry_path,
        "audit_path": audit_path,
        "unit_folders_root": unit_root,
        "asset_owner": "Little B's Asset",
    }
    register_manual_asset(**common, unit="305", vin="TR-17")

    with pytest.raises(AssetValidationError, match="VIN/serial number is already assigned"):
        register_manual_asset(**common, unit="306", vin="tr17")

    assert not (unit_root / "Unit_306").exists()


def test_database_rebuild_restores_manual_assets_and_ownership(tmp_path):
    workbook_path = _workbook(tmp_path)
    database_path = tmp_path / "fleet.db"
    registry_path = tmp_path / "manual_assets.json"
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    farm_root = tmp_path / "Farm Assets"
    farm_root.mkdir()
    import_fleet_workbook(workbook_path, database_path)
    register_manual_asset(
        database_path=database_path,
        registry_path=registry_path,
        audit_path=tmp_path / "audit.jsonl",
        unit_folders_root=unit_root,
        farm_asset_folders_root=farm_root,
        unit="305",
        asset_owner="Farm Asset",
        plate="FARM305",
    )

    import_fleet_workbook(workbook_path, database_path, manual_assets_path=registry_path)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT asset_owner, asset_source, plate FROM units WHERE normalized_unit = '305'"
        ).fetchone()
    assert row == ("Farm Asset", "manual", "FARM305")


def test_registered_asset_is_identified_by_plate_with_owner(tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    farm_root = tmp_path / "Farm Assets"
    farm_root.mkdir()
    register_manual_asset(
        database_path=database_path,
        registry_path=tmp_path / "manual_assets.json",
        audit_path=tmp_path / "audit.jsonl",
        unit_folders_root=unit_root,
        farm_asset_folders_root=farm_root,
        unit="305",
        asset_owner="Farm Asset",
        plate="FARM305",
    )

    match = match_units_in_text(database_path, "Service invoice for tag FARM-305")

    assert match["status"] == "unique"
    assert match["units"] == ["305"]
    assert find_asset_owner(database_path, "305") == "Farm Asset"


def test_registry_write_failure_leaves_no_asset_or_unit_folder(monkeypatch, tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    monkeypatch.setattr(assets, "_write_registry", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("forced")))

    with pytest.raises(OSError, match="forced"):
        register_manual_asset(
            database_path=database_path,
            registry_path=tmp_path / "manual_assets.json",
            audit_path=tmp_path / "audit.jsonl",
            unit_folders_root=unit_root,
            unit="305",
            asset_owner="Little B's Asset",
            plate="NEW305",
        )

    assert not (unit_root / "Unit_305").exists()
    assert not list(unit_root.glob(".dotdocs-*"))
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM units WHERE normalized_unit = '305'").fetchone()[0] == 0


def test_final_audit_failure_compensates_database_registry_and_folder(monkeypatch, tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    original_append = assets._append_asset_audit

    def fail_final_audit(path, event, asset, **details):
        if event == "asset_created":
            raise OSError("audit unavailable")
        return original_append(path, event, asset, **details)

    monkeypatch.setattr(assets, "_append_asset_audit", fail_final_audit)
    with pytest.raises(OSError, match="audit unavailable"):
        register_manual_asset(
            database_path=database_path,
            registry_path=tmp_path / "manual_assets.json",
            audit_path=tmp_path / "audit.jsonl",
            unit_folders_root=unit_root,
            unit="305",
            asset_owner="Little B's Asset",
            plate="NEW305",
        )

    assert not (unit_root / "Unit_305").exists()
    assert json.loads((tmp_path / "manual_assets.json").read_text(encoding="utf-8")) == []
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM units WHERE normalized_unit = '305'").fetchone()[0] == 0


def test_concurrent_duplicate_registration_allows_exactly_one_asset(tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    registry_path = tmp_path / "manual_assets.json"
    audit_path = tmp_path / "audit.jsonl"
    barrier = threading.Barrier(2)

    def register(plate):
        barrier.wait()
        return register_manual_asset(
            database_path=database_path,
            registry_path=registry_path,
            audit_path=audit_path,
            unit_folders_root=unit_root,
            unit="305",
            asset_owner="Little B's Asset",
            plate=plate,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(register, plate) for plate in ("RACE305A", "RACE305B")]
    outcomes = []
    for future in futures:
        try:
            outcomes.append(("success", future.result()))
        except AssetValidationError as error:
            outcomes.append(("rejected", str(error)))

    assert [kind for kind, _value in outcomes].count("success") == 1
    assert [kind for kind, _value in outcomes].count("rejected") == 1
    assert len(json.loads(registry_path.read_text(encoding="utf-8"))) == 1
    with sqlite3.connect(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM units WHERE normalized_unit = '305'").fetchone()[0] == 1
    assert (unit_root / "Unit_305").is_dir()
    events = [json.loads(line)["event"] for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert events.count("asset_created") == 1


def test_failed_registration_holds_lock_through_registry_compensation(monkeypatch, tmp_path):
    database_path = _database(tmp_path)
    unit_root = tmp_path / "In Use"
    unit_root.mkdir()
    registry_path = tmp_path / "manual_assets.json"
    audit_path = tmp_path / "audit.jsonl"
    compensation_waiting = threading.Event()
    release_compensation = threading.Event()
    success_started = threading.Event()
    success_done = threading.Event()
    errors = {}
    original_write = assets._write_registry
    original_audit = assets._append_asset_audit

    def delayed_write(path, records):
        if threading.current_thread().name == "failing-registration" and records == []:
            compensation_waiting.set()
            assert release_compensation.wait(timeout=5)
        return original_write(path, records)

    def fail_first_final_audit(path, event, asset, **details):
        if event == "asset_created" and asset["unit"] == "305":
            raise OSError("forced final audit failure")
        return original_audit(path, event, asset, **details)

    monkeypatch.setattr(assets, "_write_registry", delayed_write)
    monkeypatch.setattr(assets, "_append_asset_audit", fail_first_final_audit)

    def failing_registration():
        try:
            register_manual_asset(
                database_path=database_path,
                registry_path=registry_path,
                audit_path=audit_path,
                unit_folders_root=unit_root,
                unit="305",
                asset_owner="Little B's Asset",
                plate="FAIL305",
            )
        except Exception as error:
            errors["failing"] = error

    def successful_registration():
        success_started.set()
        try:
            register_manual_asset(
                database_path=database_path,
                registry_path=registry_path,
                audit_path=audit_path,
                unit_folders_root=unit_root,
                unit="306",
                asset_owner="Little B's Asset",
                plate="GOOD306",
            )
        except Exception as error:
            errors["successful"] = error
        finally:
            success_done.set()

    failing_thread = threading.Thread(target=failing_registration, name="failing-registration")
    failing_thread.start()
    assert compensation_waiting.wait(timeout=5)
    successful_thread = threading.Thread(target=successful_registration, name="successful-registration")
    successful_thread.start()
    assert success_started.wait(timeout=5)
    completed_during_compensation = success_done.wait(timeout=0.5)
    release_compensation.set()
    failing_thread.join(timeout=5)
    successful_thread.join(timeout=5)

    assert not completed_during_compensation
    assert isinstance(errors.get("failing"), OSError)
    assert "successful" not in errors
    assert [record["unit"] for record in json.loads(registry_path.read_text(encoding="utf-8"))] == ["306"]
    with sqlite3.connect(database_path) as connection:
        units = [row[0] for row in connection.execute(
            "SELECT normalized_unit FROM units WHERE asset_source = 'manual' ORDER BY normalized_unit"
        )]
    assert units == ["306"]
    assert not (unit_root / "Unit_305").exists()
    assert (unit_root / "Unit_306").is_dir()
