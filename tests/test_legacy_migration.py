import hashlib
import json
from pathlib import Path

import pytest

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
