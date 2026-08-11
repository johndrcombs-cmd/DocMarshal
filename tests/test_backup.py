import hashlib
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from backup_dot_pilot import BackupError, create_backup


def test_create_backup_publishes_verified_snapshot_with_manifest(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    manual_assets = state / "manual_assets.json"
    active_review = state / "active_review.json"
    audit = state / "audit.jsonl"
    fleet_database = state / "fleet.db"
    manual_assets.write_text('[{"unit": "1000"}]\n', encoding="utf-8")
    active_review.write_text('[{"source_file": "scan.pdf", "status": "approved"}]\n', encoding="utf-8")
    audit.write_text('{"event": "approved"}\n', encoding="utf-8")
    with sqlite3.connect(fleet_database) as connection:
        connection.execute("CREATE TABLE units(unit TEXT)")
        connection.execute("INSERT INTO units VALUES ('1000')")

    snapshot = create_backup(
        backup_root=tmp_path / "backups",
        json_files={
            "manual_assets.json": manual_assets,
            "active_review.json": active_review,
        },
        jsonl_files={"audit.jsonl": audit},
        sqlite_files={"fleet.db": fleet_database},
        now=datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc),
        retention_days=90,
    )

    assert snapshot.name == "2026-07-28_180000"
    assert not list((tmp_path / "backups").glob(".tmp-*"))
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert set(manifest["files"]) == {
        "active_review.json",
        "audit.jsonl",
        "fleet.db",
        "manual_assets.json",
    }
    for name, details in manifest["files"].items():
        copied = snapshot / name
        assert copied.is_file()
        assert copied.stat().st_size == details["size"]
        assert hashlib.sha256(copied.read_bytes()).hexdigest() == details["sha256"]
    with sqlite3.connect(snapshot / "fleet.db") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT unit FROM units").fetchone()[0] == "1000"


def test_create_backup_fails_closed_for_invalid_audit_log(tmp_path):
    source = tmp_path / "audit.jsonl"
    source.write_text('{"event": "approved"}\n{"event":', encoding="utf-8")
    backup_root = tmp_path / "backups"

    with pytest.raises(BackupError, match="invalid JSON"):
        create_backup(
            backup_root=backup_root,
            json_files={},
            jsonl_files={"audit.jsonl": source},
            sqlite_files={},
            now=datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc),
        )

    assert not list(backup_root.glob("20*"))
    assert not list(backup_root.glob(".tmp-*"))
