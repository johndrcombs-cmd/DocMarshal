import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import pytest

import dotdocs.legacy_migration as legacy_migration
from dotdocs.legacy_migration import migrate_legacy_approved_sources


def _approved_record(source: Path, destination: Path) -> dict:
    content = source.read_bytes()
    return {
        "source_file": str(source),
        "source_sha256": hashlib.sha256(content).hexdigest(),
        "source_size": len(content),
        "status": "approved",
        "approved_destination": str(destination),
    }


def test_legacy_approved_migration_dry_run_identifies_without_moving(tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=True,
    )

    assert summary["eligible"] == 1
    assert summary["migrated"] == 0
    assert summary["blocked"] == []
    assert source.is_file()
    assert not approved.exists()


def test_legacy_approved_migration_moves_verified_source_and_updates_session(tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    session = tmp_path / "Review" / "active_review.json"
    audit = tmp_path / "Review" / "audit.jsonl"

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=session, audit_path=audit, dry_run=False,
    )

    assert summary["migrated"] == 1
    assert not source.exists()
    archived = approved / "scan.pdf"
    assert archived.read_bytes() == destination.read_bytes()
    saved = json.loads(session.read_text(encoding="utf-8"))
    assert saved[0]["approved_archived_file"] == str(archived)
    events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events[-1] == "legacy_approved_archived"


def test_legacy_approved_migration_blocks_production_fingerprint_mismatch(tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(b"different production content")
    record = _approved_record(source, destination)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 0
    assert len(summary["blocked"]) == 1
    assert "production copy fingerprint" in summary["blocked"][0]["reason"]
    assert source.is_file()
    assert not approved.exists()
    saved = json.loads((tmp_path / "Review" / "active_review.json").read_text(encoding="utf-8"))
    assert saved[0]["status"] == "needs_review"
    assert saved[0]["previous_status"] == "approved"
    assert saved[0]["migration_blocked_reason"]


def test_legacy_migration_rollback_preserves_replacement_archive(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    replacement = b"replacement created by another process"
    real_lock = legacy_migration._lock_verified_owned_file

    @contextmanager
    def replace_before_archive_lock(path, *args, **kwargs):
        if Path(path).parent == approved:
            Path(path).write_bytes(replacement)
            raise OSError("injected archive-lock failure")
        with real_lock(path, *args, **kwargs) as handle:
            yield handle

    monkeypatch.setattr(legacy_migration, "_lock_verified_owned_file", replace_before_archive_lock)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 0
    assert source.read_bytes() == b"verified approved PDF"
    assert (approved / "scan.pdf").read_bytes() == replacement
    assert "Refusing to remove a production file that changed" in summary["blocked"][0]["reason"]


def test_legacy_migration_exclusively_locks_archive_through_commit(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    replacement_errors = []
    save_calls = 0

    def attempt_replacement_during_commit(_session, _results):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            try:
                (approved / "scan.pdf").write_bytes(b"FOREIGN REPLACEMENT")
            except OSError as error:
                replacement_errors.append(error)
            else:
                raise AssertionError("archive replacement unexpectedly succeeded while commit lock was held")
        raise OSError("injected persistence failure")

    monkeypatch.setattr(
        legacy_migration,
        "save_review_session",
        attempt_replacement_during_commit,
    )

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 0
    assert replacement_errors
    assert source.read_bytes() == b"verified approved PDF"
    assert not (approved / "scan.pdf").exists()


def test_legacy_migration_binds_archive_identity_before_publication(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    owned_moved_aside = approved / "owned-moved-aside.pdf"
    foreign = b"verified approved PDF"
    real_rename = Path.rename

    def replace_immediately_after_publication(path, target):
        result = real_rename(path, target)
        target = Path(target)
        if ".docmarshal-copy-" in path.name and target.suffix.casefold() == ".pdf":
            real_rename(target, owned_moved_aside)
            target.write_bytes(foreign)
        return result

    monkeypatch.setattr(Path, "rename", replace_immediately_after_publication)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 0
    assert source.read_bytes() == b"verified approved PDF"
    assert (approved / "scan.pdf").read_bytes() == foreign
    assert owned_moved_aside.read_bytes() == b"verified approved PDF"


def test_legacy_migration_locks_source_quarantine_until_handle_retirement(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    replacement_errors = []
    real_append_audit = legacy_migration._append_audit

    def attempt_source_replacement_during_success_audit(path, event):
        if event.get("event") == "legacy_approved_archive_prepared":
            quarantine = next(incoming.glob(".*.docmarshal-legacy-*.tmp"))
            try:
                quarantine.rename(incoming / "owned-moved-aside.tmp")
                quarantine.write_bytes(b"FOREIGN SOURCE REPLACEMENT")
            except OSError as error:
                replacement_errors.append(error)
        return real_append_audit(path, event)

    monkeypatch.setattr(legacy_migration, "_append_audit", attempt_source_replacement_during_success_audit)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 1
    assert replacement_errors
    assert not source.exists()
    assert not (incoming / "owned-moved-aside.tmp").exists()
    assert not list(incoming.glob(".*.docmarshal-legacy-*.tmp"))
    assert (approved / "scan.pdf").read_bytes() == b"verified approved PDF"


def test_legacy_migration_source_is_locked_before_quarantine_rename(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    replacement_errors = []
    real_rename_locked = legacy_migration._rename_locked_owned_file

    def attempt_replacement_immediately_after_rename(handle, target):
        real_rename_locked(handle, target)
        target = Path(target)
        if ".docmarshal-legacy-" in target.name:
            try:
                target.rename(incoming / "owned-moved-aside.tmp")
                target.write_bytes(b"FOREIGN SOURCE REPLACEMENT")
            except OSError as error:
                replacement_errors.append(error)

    monkeypatch.setattr(
        legacy_migration,
        "_rename_locked_owned_file",
        attempt_replacement_immediately_after_rename,
    )

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 1
    assert replacement_errors
    assert not source.exists()
    assert not (incoming / "owned-moved-aside.tmp").exists()
    assert not list(incoming.glob(".*.docmarshal-legacy-*.tmp"))
    assert (approved / "scan.pdf").read_bytes() == b"verified approved PDF"


def test_legacy_migration_rejects_same_content_replacement_before_source_lock(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    owned_moved_aside = incoming / "owned-moved-aside.pdf"
    real_verify = legacy_migration._verify_candidate

    def replace_after_candidate_admission(*args, **kwargs):
        candidate = real_verify(*args, **kwargs)
        source.rename(owned_moved_aside)
        source.write_bytes(b"verified approved PDF")
        return candidate

    monkeypatch.setattr(legacy_migration, "_verify_candidate", replace_after_candidate_admission)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=tmp_path / "Review" / "audit.jsonl",
        dry_run=False,
    )

    assert summary["migrated"] == 0
    assert owned_moved_aside.read_bytes() == b"verified approved PDF"
    assert source.read_bytes() == b"verified approved PDF"
    assert not list(approved.glob("*.pdf"))


def test_legacy_migration_recovers_pending_finalization_after_source_retirement(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    session = tmp_path / "Review" / "active_review.json"
    audit = tmp_path / "Review" / "audit.jsonl"
    real_save = legacy_migration.save_review_session
    save_calls = 0

    def fail_final_publication(path, results):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 2:
            raise OSError("injected finalization failure")
        return real_save(path, results)

    monkeypatch.setattr(legacy_migration, "save_review_session", fail_final_publication)
    first = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=session, audit_path=audit, dry_run=False,
    )

    assert first["migrated"] == 0
    assert not source.exists()
    assert (approved / "scan.pdf").is_file()
    pending = json.loads(session.read_text(encoding="utf-8"))
    assert pending[0]["legacy_migration_state"] == "source_retirement_pending_finalize"

    monkeypatch.setattr(legacy_migration, "save_review_session", real_save)
    recovered = migrate_legacy_approved_sources(
        pending, incoming, approved,
        session_path=session, audit_path=audit, dry_run=False,
    )

    assert recovered["migrated"] == 1
    finalized = json.loads(session.read_text(encoding="utf-8"))
    assert "legacy_migration_state" not in finalized[0]
    events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events[-1] == "legacy_approved_archive_recovered"


def test_legacy_migration_records_compensating_event_when_final_source_delete_fails(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    session = tmp_path / "Review" / "active_review.json"
    audit = tmp_path / "Review" / "audit.jsonl"
    monkeypatch.setattr(
        legacy_migration,
        "_delete_locked_owned_file",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected locked-source retirement failure")
        ),
    )

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=session, audit_path=audit, dry_run=False,
    )

    assert summary["migrated"] == 0
    assert source.read_bytes() == b"verified approved PDF"
    assert not (approved / "scan.pdf").exists()
    events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert "legacy_approved_archive_prepared" in events
    assert events[-1] == "legacy_approved_archive_rolled_back"


def test_legacy_migration_mid_copy_failure_never_publishes_partial_archive(monkeypatch, tmp_path):
    incoming = tmp_path / "Incoming"
    approved = tmp_path / "Approved"
    incoming.mkdir()
    source = incoming / "scan.pdf"
    source.write_bytes(b"verified approved PDF")
    destination = tmp_path / "Production" / "file.pdf"
    destination.parent.mkdir()
    destination.write_bytes(source.read_bytes())
    record = _approved_record(source, destination)
    audit = tmp_path / "Review" / "audit.jsonl"

    def fail_mid_copy(_source_handle, archive_handle):
        archive_handle.write(b"partial")
        raise OSError("injected mid-copy failure")

    monkeypatch.setattr(legacy_migration.shutil, "copyfileobj", fail_mid_copy)

    summary = migrate_legacy_approved_sources(
        [record], incoming, approved,
        session_path=tmp_path / "Review" / "active_review.json",
        audit_path=audit, dry_run=False,
    )

    assert summary["migrated"] == 0
    assert source.read_bytes() == b"verified approved PDF"
    assert list(approved.glob("*.pdf")) == []
    assert list(approved.glob(".*.tmp")) == []
    events = [json.loads(line)["event"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert events[-1] == "legacy_approved_archive_rolled_back"
