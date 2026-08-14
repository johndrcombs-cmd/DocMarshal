from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import uuid

from .review import (
    ApprovalError,
    _delete_locked_owned_file,
    _file_identity,
    _lock_verified_owned_file,
    _rename_locked_owned_file,
    _remove_owned_file_via_quarantine,
    _verify_locked_owned_file,
    save_review_session,
)


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


def _remove_owned_archive(
    path: Path,
    identity: os.stat_result,
    expected_hash: str,
    expected_size: int,
) -> None:
    try:
        _remove_owned_file_via_quarantine(path, identity, expected_hash, expected_size)
    except ApprovalError as error:
        raise LegacyMigrationError(str(error)) from error


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


def _verify_candidate(
    record: dict,
    incoming: Path,
    *,
    verify_source_path: bool = True,
) -> tuple[Path, Path, str, int, os.stat_result]:
    reason = _candidate_reason(record, incoming)
    if reason:
        raise LegacyMigrationError(reason)
    source = Path(record["source_file"])
    production = Path(record.get("approved_destination") or record["proposed_destination"])
    expected_hash = str(record["source_sha256"])
    expected_size = int(record["source_size"])
    source_identity = _file_identity(source)
    if verify_source_path:
        source_hash, source_size = _sha256(source)
        if source_hash != expected_hash or source_size != expected_size:
            raise LegacyMigrationError("Incoming source fingerprint does not match the Approved review record")
    production_hash, production_size = _sha256(production)
    if production_hash != expected_hash or production_size != expected_size:
        raise LegacyMigrationError("production copy fingerprint does not match the Approved review record")
    return source, production, expected_hash, expected_size, source_identity


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
        if record.get("legacy_migration_state") == "source_retirement_pending_finalize":
            source = Path(record.get("source_file", ""))
            archive = Path(record.get("approved_archived_file", ""))
            quarantine = Path(record.get("legacy_quarantine_file", ""))
            expected_hash = str(record.get("source_sha256", ""))
            expected_size = int(record.get("source_size", -1))
            summary["eligible"] += 1
            if dry_run:
                continue
            try:
                if not archive.is_file() or _sha256(archive) != (expected_hash, expected_size):
                    raise LegacyMigrationError("pending legacy archive is missing or changed")
                if not source.exists() and not quarantine.exists():
                    finalized = dict(record)
                    finalized.pop("legacy_migration_state", None)
                    finalized.pop("legacy_quarantine_file", None)
                    working[index] = finalized
                    save_review_session(session, working)
                    _append_audit(
                        audit,
                        {
                            "event": "legacy_approved_archive_recovered",
                            "source_file": str(source),
                            "archived_file": str(archive),
                            "source_sha256": expected_hash,
                        },
                    )
                    record.update(finalized)
                    summary["migrated"] += 1
                    continue
                if quarantine.is_file() and not source.exists():
                    with _lock_verified_owned_file(
                        quarantine,
                        None,
                        expected_hash,
                        expected_size,
                        delete_access=True,
                        verify_on_exit=False,
                    ) as recovery_handle:
                        _rename_locked_owned_file(recovery_handle, source)
                blocked = dict(record)
                blocked["previous_status"] = record.get("status")
                blocked["status"] = "needs_review"
                blocked["migration_blocked_reason"] = (
                    "Interrupted legacy migration was restored or retained for manual review"
                )
                blocked["recovery_archive_file"] = str(archive)
                blocked.pop("approved_archived_file", None)
                blocked.pop("legacy_migration_state", None)
                blocked.pop("legacy_quarantine_file", None)
                working[index] = blocked
                save_review_session(session, working)
                _append_audit(
                    audit,
                    {
                        "event": "legacy_approved_archive_recovery_blocked",
                        "source_file": str(source),
                        "recovery_archive_file": str(archive),
                        "source_sha256": expected_hash,
                    },
                )
                record.update(blocked)
                summary["blocked"].append(
                    {"source_file": str(source), "reason": blocked["migration_blocked_reason"]}
                )
            except Exception as error:
                summary["blocked"].append({"source_file": str(source), "reason": str(error)})
            continue
        if record.get("status") != "approved":
            summary["skipped"] += 1
            continue
        reason = _candidate_reason(record, incoming)
        if reason == "already archived" or reason == "Incoming source is missing":
            summary["skipped"] += 1
            continue
        try:
            source, production, expected_hash, expected_size, source_identity = _verify_candidate(
                record,
                incoming,
                verify_source_path=dry_run,
            )
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
        archive_staging = approved / f".{source.name}.docmarshal-copy-{uuid.uuid4().hex}.tmp"
        quarantine = incoming / f".{source.name}.docmarshal-legacy-{uuid.uuid4().hex}.tmp"
        original_working = [dict(item) for item in working]
        archive_staged = False
        archive_created = False
        archive_identity: os.stat_result | None = None
        source_quarantined = False
        source_retired = False
        pending_persisted = False
        try:
            with _lock_verified_owned_file(
                source,
                source_identity,
                expected_hash,
                expected_size,
                delete_access=True,
                verify_on_exit=False,
            ) as source_handle:
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
                    source_handle.seek(0)
                    with archive_staging.open("xb") as archive_handle:
                        archive_staged = True
                        shutil.copyfileobj(source_handle, archive_handle)
                        archive_handle.flush()
                        os.fsync(archive_handle.fileno())
                    if _sha256(archive_staging) != (expected_hash, expected_size):
                        raise LegacyMigrationError("Approved archive copy failed fingerprint verification")
                    _verify_locked_owned_file(
                        source_handle, source, source_identity, expected_hash, expected_size
                    )
                    archive_identity = _file_identity(archive_staging)
                    while True:
                        archive = _archive_destination(approved, source)
                        try:
                            archive_staging.rename(archive)
                        except FileExistsError:
                            continue
                        break
                    archive_staged = False
                    archive_created = True
                    with _lock_verified_owned_file(
                        archive,
                        archive_identity,
                        expected_hash,
                        expected_size,
                        verify_on_exit=False,
                    ) as archive_handle:
                        _rename_locked_owned_file(source_handle, quarantine)
                        source_quarantined = True
                        pending = dict(record)
                        pending["approved_archived_file"] = str(archive)
                        pending["legacy_archived_at_utc"] = datetime.now(timezone.utc).isoformat()
                        pending["legacy_migration_state"] = "source_retirement_pending_finalize"
                        pending["legacy_quarantine_file"] = str(quarantine)
                        working[index] = pending
                        save_review_session(session, working)
                        pending_persisted = True
                        _append_audit(
                            audit,
                            {
                                "event": "legacy_approved_archive_prepared",
                                "source_file": str(source),
                                "archived_file": str(archive),
                                "destination": str(production),
                                "source_sha256": expected_hash,
                            },
                        )
                        _verify_locked_owned_file(
                            archive_handle, archive, archive_identity, expected_hash, expected_size
                        )
                        _verify_locked_owned_file(
                            source_handle, quarantine, source_identity, expected_hash, expected_size
                        )
                        _delete_locked_owned_file(source_handle, quarantine)
                        source_quarantined = False
                        source_retired = True
                except Exception as transaction_error:
                    if source_quarantined:
                        try:
                            if source.exists():
                                raise LegacyMigrationError("Incoming path was occupied during rollback")
                            _rename_locked_owned_file(source_handle, source)
                            source_quarantined = False
                        except Exception as restore_error:
                            raise LegacyMigrationError(
                                f"{transaction_error}; source restore failed: {restore_error}; "
                                f"owned source retained at {quarantine}"
                            ) from transaction_error
                    raise
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
            record.update(updated)
            summary["migrated"] += 1
        except Exception as error:
            rollback_errors = []
            if source_quarantined:
                rollback_errors.append(f"source restore incomplete; owned source retained at {quarantine}")
            if archive_staged:
                try:
                    archive_staging.unlink(missing_ok=True)
                    archive_staged = False
                except Exception as rollback_error:
                    rollback_errors.append(f"archive staging cleanup failed: {rollback_error}")
            if archive_created and not source_retired:
                try:
                    _remove_owned_archive(
                        archive,
                        archive_identity,
                        expected_hash,
                        expected_size,
                    )
                except Exception as rollback_error:
                    rollback_errors.append(f"archive cleanup failed: {rollback_error}")
            if not source_retired:
                working = original_working
                try:
                    save_review_session(session, working)
                except Exception as rollback_error:
                    rollback_errors.append(f"session rollback failed: {rollback_error}")
            reason = str(error)
            if source_retired:
                reason += "; source retirement completed and finalization remains pending"
            if rollback_errors:
                reason += "; " + "; ".join(rollback_errors)
            try:
                _append_audit(
                    audit,
                    {
                        "event": (
                            "legacy_approved_archive_finalize_pending"
                            if source_retired
                            else "legacy_approved_archive_rolled_back"
                        ),
                        "source_file": str(source),
                        "archived_file": str(archive),
                        "destination": str(production),
                        "source_sha256": expected_hash,
                        "reason": reason,
                        "pending_persisted": pending_persisted,
                    },
                )
            except Exception as audit_error:
                reason += f"; compensation audit failed: {audit_error}"
            summary["blocked"].append({"source_file": record.get("source_file"), "reason": reason})
    return summary
