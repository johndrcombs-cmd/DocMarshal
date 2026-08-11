from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path


class BackupError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_json(data: bytes, path: Path) -> None:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise BackupError(f"JSON source is invalid: {path}: {error}") from error
    if not isinstance(value, (list, dict)):
        raise BackupError(f"JSON source must contain a list or object: {path}")


def _validate_jsonl(data: bytes, path: Path) -> None:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BackupError(f"Audit source is not UTF-8: {path}: {error}") from error
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise BackupError(
                f"Audit source has invalid JSON on line {line_number}: {path}: {error}"
            ) from error
        if not isinstance(value, dict):
            raise BackupError(
                f"Audit source line {line_number} is not a JSON object: {path}"
            )


def _stable_read(path: Path, validator, attempts: int = 4) -> bytes:
    if not path.is_file():
        raise BackupError(f"Required backup source is unavailable: {path}")
    for attempt in range(attempts):
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
        if before.st_size == after.st_size and before.st_mtime_ns == after.st_mtime_ns:
            validator(data, path)
            return data
        if attempt + 1 < attempts:
            time.sleep(0.1 * (attempt + 1))
    raise BackupError(f"Backup source kept changing and could not be captured safely: {path}")


def _write_bytes(path: Path, data: bytes) -> None:
    with path.open("xb") as destination:
        destination.write(data)
        destination.flush()
        os.fsync(destination.fileno())


def _backup_sqlite(source_path: Path, destination_path: Path) -> None:
    if not source_path.is_file():
        raise BackupError(f"Required SQLite source is unavailable: {source_path}")
    with closing(sqlite3.connect(source_path, timeout=30)) as source, closing(
        sqlite3.connect(destination_path)
    ) as destination:
        source.backup(destination)
        destination.commit()
        if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError(f"SQLite backup failed integrity validation: {source_path}")


def _unique_snapshot_path(backup_root: Path, timestamp: str) -> Path:
    candidate = backup_root / timestamp
    sequence = 2
    while candidate.exists():
        candidate = backup_root / f"{timestamp}_{sequence}"
        sequence += 1
    return candidate


def _remove_expired_snapshots(backup_root: Path, now: datetime, retention_days: int) -> None:
    cutoff = now - timedelta(days=retention_days)
    for path in backup_root.iterdir():
        if not path.is_dir() or path.name.startswith(".tmp-"):
            continue
        try:
            timestamp = datetime.strptime(path.name[:17], "%Y-%m-%d_%H%M%S")
        except ValueError:
            continue
        if now.tzinfo is not None:
            timestamp = timestamp.replace(tzinfo=now.tzinfo)
        if timestamp < cutoff:
            shutil.rmtree(path)


def create_backup(
    *,
    backup_root: str | Path,
    json_files: dict[str, str | Path],
    jsonl_files: dict[str, str | Path],
    sqlite_files: dict[str, str | Path],
    now: datetime | None = None,
    retention_days: int = 90,
) -> Path:
    if retention_days < 1:
        raise BackupError("Retention must be at least one day.")
    now = now or datetime.now().astimezone()
    backup_root = Path(backup_root)
    backup_root.mkdir(parents=True, exist_ok=True)
    if not backup_root.is_dir():
        raise BackupError(f"Backup root is unavailable: {backup_root}")

    timestamp = now.strftime("%Y-%m-%d_%H%M%S")
    snapshot = _unique_snapshot_path(backup_root, timestamp)
    staging = backup_root / f".tmp-{timestamp}-{os.getpid()}"
    if staging.exists():
        raise BackupError(f"Backup staging path already exists: {staging}")
    staging.mkdir()
    manifest_files: dict[str, dict] = {}
    try:
        for name, source_value in json_files.items():
            source = Path(source_value)
            data = _stable_read(source, _validate_json)
            destination = staging / name
            _write_bytes(destination, data)
            manifest_files[name] = {
                "source": str(source),
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
                "type": "json",
            }
        for name, source_value in jsonl_files.items():
            source = Path(source_value)
            data = _stable_read(source, _validate_jsonl)
            destination = staging / name
            _write_bytes(destination, data)
            manifest_files[name] = {
                "source": str(source),
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
                "type": "jsonl",
            }
        for name, source_value in sqlite_files.items():
            source = Path(source_value)
            destination = staging / name
            _backup_sqlite(source, destination)
            manifest_files[name] = {
                "source": str(source),
                "size": destination.stat().st_size,
                "sha256": _sha256(destination),
                "type": "sqlite",
            }

        manifest = {
            "schema_version": 1,
            "status": "complete",
            "created_at": now.isoformat(),
            "retention_days": retention_days,
            "files": manifest_files,
        }
        manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        _write_bytes(staging / "manifest.json", manifest_data)
        staging.rename(snapshot)
        _remove_expired_snapshots(backup_root, now, retention_days)
        return snapshot
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _append_log(backup_root: Path, message: str) -> None:
    try:
        backup_root.mkdir(parents=True, exist_ok=True)
        with (backup_root / "backup.log").open("a", encoding="utf-8") as log:
            log.write(message.rstrip() + "\n")
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up DocMarshal pilot state.")
    parser.add_argument(
        "--backup-root",
        default=r"C:\DocMarshal\Backups",
    )
    parser.add_argument("--retention-days", type=int, default=90)
    args = parser.parse_args(argv)

    project = Path(__file__).resolve().parent
    config_path = project / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        review_folder = Path(config["scan_review"])
        snapshot = create_backup(
            backup_root=args.backup_root,
            json_files={
                "manual_assets.json": Path(config["manual_assets_registry"]),
                "active_review.json": review_folder / "active_review.json",
                "config.json": config_path,
            },
            jsonl_files={"audit.jsonl": review_folder / "audit.jsonl"},
            sqlite_files={"fleet.db": Path(config["fleet_database"])},
            retention_days=args.retention_days,
        )
    except Exception as error:
        message = f"{datetime.now().astimezone().isoformat()} FAILED: {error}"
        _append_log(Path(args.backup_root), message)
        print(message, file=sys.stderr)
        return 1
    message = f"{datetime.now().astimezone().isoformat()} OK: {snapshot}"
    _append_log(Path(args.backup_root), message)
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
