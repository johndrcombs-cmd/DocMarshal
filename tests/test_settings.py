import json

import pytest

from dotdocs.settings import SETTING_DEFINITIONS, save_user_settings


def test_settings_cover_user_specific_paths_and_preserve_unknown_values(tmp_path):
    config_path = tmp_path / "config.json"
    current = {
        "scan_incoming": "C:/Old/Incoming",
        "scan_processed": "C:/Old/Processed",
        "scan_approved": "C:/Old/Approved",
        "scan_exceptions": "C:/Old/Exceptions",
        "scan_review": "C:/Old/Review",
        "fleet_workbook": "C:/Old/fleet.xlsx",
        "fleet_database": "C:/Old/fleet.db",
        "manual_assets_registry": "C:/Old/manual.json",
        "unit_folders_root": "C:/Old/Fleet",
        "farm_asset_folders_root": "C:/Old/Farm",
        "tool_folders_root": "C:/Old/Tools",
        "deployment_specific_value": "preserve-me",
    }
    config_path.write_text(json.dumps(current), encoding="utf-8")

    updated = save_user_settings(
        config_path,
        current,
        {"scan_incoming": "D:/DocMarshal/Incoming", "unit_folders_root": "D:/Fleet"},
    )

    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated == persisted
    assert persisted["scan_incoming"] == "D:/DocMarshal/Incoming"
    assert persisted["unit_folders_root"] == "D:/Fleet"
    assert persisted["deployment_specific_value"] == "preserve-me"
    assert {definition["key"] for definition in SETTING_DEFINITIONS} >= {
        "scan_incoming",
        "scan_processed",
        "scan_exceptions",
        "scan_review",
        "fleet_database",
        "unit_folders_root",
        "farm_asset_folders_root",
        "tool_folders_root",
    }


def test_settings_reject_blank_paths_without_touching_config(tmp_path):
    config_path = tmp_path / "config.json"
    current = {definition["key"]: f"C:/{definition['key']}" for definition in SETTING_DEFINITIONS}
    config_path.write_text(json.dumps(current, sort_keys=True), encoding="utf-8")
    before = config_path.read_bytes()

    with pytest.raises(ValueError, match="Incoming Documents"):
        save_user_settings(config_path, current, {"scan_incoming": "  "})

    assert config_path.read_bytes() == before
