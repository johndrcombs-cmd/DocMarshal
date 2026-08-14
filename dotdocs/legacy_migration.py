from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .review import save_review_session


class LegacyMigrationError(RuntimeError):
    pass


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _append_audit(path: Path, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _archive_destination(approved: Path, source: Path) -> Path:
    destination = approved / source.name
    sequence = 2
    while destination.exists():
        destination = approved / f"{source.stem}_{sequence}{source.suffix}"
        sequence += 1
    return destination


def _candidate_reason(record: dict, incoming: Path) -> str | None:
    if record.get("status") != "approved":
        return "not an Approved record"
    archived = record.get("approved_archived_file")
    if archived and Path(archived).is_file():
        return "already archived"
    source_value = record.get("source_file")
    if not source_value:
        return "source path is missing"
    source = Path(source_value)
    if not source.is_file():
        return "Incoming source is missing"
    try:
        if source.resolve().parent != incoming.resolve():
            return "source is not directly inside Incoming"
    except OSError:
        return "source path could not be resolved"
    if source.suffix.casefold() != ".pdf":
        return "source is not a PDF"
    if not record.get("source_sha256") or record.get("source_size") is None:
        return "saved review fingerprint is missing"
    destination_value = record.get("approved_destination") or record.get("proposed_destination")
    if not destination_value or not Path(destination_value).is_file():
        return "approved production copy is missing"
    return None


def _verify_candidate(record: dict, incoming: Path) -> tuple[Path, Path, str, int]:
    reason = _candidate_reason(record, incoming)
    if reason:
        raise LegacyMigrationError(reason)
    source = Path(record["source_file"])
    production = Path(record.get("approved_destination") or record["proposed_destination"])
    expected_hash = str(record["source_sha256"])
    expected_size = int(record["source_size"])
    source_hash, source_size = _sha256(source)
    if source_hash != expected_hash or source_size != expected_size:
        raise LegacyMigrationError("Incoming source fingerprint does not match the Approved review record")
    production_hash, production_size = _sha256(production)
    if production_hash != expected_hash or production_size != expected_size:
        raise LegacyMigrationError("production copy fingerprint does not match the Approved review record")
    return source, production, expected_hash, expected_size


def migrate_legacy_approved_sources(
    results: list[dict],
    incoming_folder: str | Path,
    approved_folder: str | Path,
    *,
    session_path: str | Path,
    audit_path: str | Path,
    dry_run: bool = True,
) -> dict:
    incoming = Path(incoming_folder)
    approved = Path(approved_folder)
    session = Path(session_path)
    audit = Path(audit_path)
    if not incoming.is_dir():
        raise LegacyMigrationError(f"Incoming folder is unavailable: {incoming}")
    if incoming.resolve() == approved.resolve():
        raise LegacyMigrationError("Incoming and Approved folders must be different")

    summary = {"eligible": 0, "migrated": 0, "blocked": [], "skipped": 0}
    working = [dict(record) for record in results]
    for index, record in enumerate(working):
        if record.get("status") != "approved":
            summary["skipped"] += 1
            continue
        reason = _candidate_reason(record, incoming)
        if reason == "already archived" or reason == "Incoming source is missing":
            summary["skipped"] += 1
            continue
        try:
            source, production, expected_hash, expected_size = _verify_candidate(record, incoming)
        except LegacyMigrationError as error:
            reason = str(error)
            if not dry_run:
                updated = dict(record)
                updated["previous_status"] = record.get("status")
                updated["status"] = "needs_review"
                updated["migration_blocked_reason"] = reason
                updated["reasons"] = list(dict.fromkeys([*record.get("reasons", []), "LEGACY_APPROVED_ARCHIVE_BLOCKED"]))
                working[index] = updated
                try:
                    save_review_session(session, working)
                    _append_audit(
                        audit,
                        {
                            "event": "legacy_approved_archive_blocked",
                            "source_file": record.get("source_file"),
                            "destination": record.get("approved_destination") or record.get("proposed_destination"),
                            "reason": reason,
                        },
                    )
                    record.update(updated)
                except Exception as persistence_error:
                    reason += f"; blocked-record persistence failed: {persistence_error}"
            summary["blocked"].append({"source_file": record.get("source_file"), "reason": reason})
            continue
        summary["eligible"] += 1
        if dry_run:
            continue

        approved.mkdir(parents=True, exist_ok=True)
        archive = _archive_destination(approved, source)
        quarantine = incoming / f".{source.name}.docmarshal-legacy-{uuid.uuid4().hex}.tmp"
        original_working = [dict(item) for item in working]
        archive_created = False
        source_quarantined = False
        try:
            _append_audit(
                audit,
                {
                    "event": "legacy_approved_archive_started",
                    "source_file": str(source),
                    "destination": str(production),
                    "source_sha256": expected_hash,
                },
            )
            with source.open("rb") as source_handle, archive.open("xb") as archive_handle:
                shutil.copyfileobj(source_handle, archive_handle)
                archive_handle.flush()
                os.fsync(archive_handle.fileno())
            archive_created = True
            if _sha256(archive) != (expected_hash, expected_size):
                raise LegacyMigrationError("Approved archive copy failed fingerprint verification")
            source.rename(quarantine)
            source_quarantined = True
            if _sha256(quarantine) != (expected_hash, expected_size):
                raise LegacyMigrationError("Incoming source changed during legacy migration")
            updated = dict(record)
            updated["approved_archived_file"] = str(archive)
            updated["legacy_archived_at_utc"] = datetime.now(timezone.utc).isoformat()
            working[index] = updated
            save_review_session(session, working)
            _append_audit(
                audit,
                {
                    "event": "legacy_approved_archived",
                    "source_file": str(source),
                    "archived_file": str(archive),
                    "destination": str(production),
                    "source_sha256": expected_hash,
                },
            )
            quarantine.unlink()
            source_quarantined = False
            record.update(updated)
            summary["migrated"] += 1
        except Exception as error:
            rollback_errors = []
            if source_quarantined:
                try:
                    if source.exists():
                        raise LegacyMigrationError("Incoming path was occupied during rollback")
                    quarantine.rename(source)
                    source_quarantined = False
                except Exception as rollback_error:
                    rollback_errors.append(f"source restore failed: {rollback_error}")
            if archive_created:
                try:
                    archive.unlink(missing_ok=True)
                except Exception as rollback_error:
                    rollback_errors.append(f"archive cleanup failed: {rollback_error}")
            working = original_working
            try:
                save_review_session(session, working)
            except Exception as rollback_error:
                rollback_errors.append(f"session rollback failed: {rollback_error}")
            reason = str(error)
            if rollback_errors:
                reason += "; " + "; ".join(rollback_errors)
            summary["blocked"].append({"source_file": record.get("source_file"), "reason": reason})
    return summary
