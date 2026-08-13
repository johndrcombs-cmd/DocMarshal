from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


SETTING_DEFINITIONS = (
    {"key": "scan_incoming", "label": "Incoming Documents", "kind": "directory"},
    {"key": "scan_processed", "label": "Processed Documents", "kind": "directory"},
    {"key": "scan_approved", "label": "Approved Documents", "kind": "directory"},
    {"key": "scan_exceptions", "label": "Exception Documents", "kind": "directory"},
    {"key": "scan_review", "label": "Review Data", "kind": "directory"},
    {"key": "fleet_workbook", "label": "Default Fleet Import Workbook", "kind": "file"},
    {"key": "fleet_database", "label": "DocMarshal Fleet Database", "kind": "file"},
    {"key": "manual_assets_registry", "label": "Legacy Manual Asset Registry", "kind": "file"},
    {"key": "unit_folders_root", "label": "Company Virtual Binder Root", "kind": "directory"},
    {"key": "farm_asset_folders_root", "label": "Farm Virtual Binder Root", "kind": "directory"},
    {"key": "tool_folders_root", "label": "Tool Virtual Binder Root", "kind": "directory"},
)


def save_user_settings(
    config_path: str | Path,
    current_config: dict,
    updates: dict[str, object],
) -> dict:
    definitions = {item["key"]: item for item in SETTING_DEFINITIONS}
    unknown = set(updates) - set(definitions)
    if unknown:
        raise ValueError(f"Unsupported setting: {sorted(unknown)[0]}")
    merged = dict(current_config)
    for key, value in updates.items():
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{definitions[key]['label']} cannot be blank.")
        if len(text) > 1000:
            raise ValueError(f"{definitions[key]['label']} is too long.")
        merged[key] = text
    for key, definition in definitions.items():
        if not str(merged.get(key) or "").strip():
            raise ValueError(f"{definition['label']} cannot be blank.")

    destination = Path(config_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=destination.name + ".",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump(merged, temporary, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return merged
