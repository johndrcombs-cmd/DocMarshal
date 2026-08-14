import os

from dotdocs.intake_index import source_snapshot, source_snapshot_matches


def test_source_snapshot_matches_unchanged_file(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"A" * 200_000)
    snapshot = source_snapshot(source)

    assert source_snapshot_matches(source, snapshot)


def test_source_snapshot_rejects_reused_filename_with_same_size_and_mtime(tmp_path):
    source = tmp_path / "scan.pdf"
    source.write_bytes(b"A" * 200_000)
    snapshot = source_snapshot(source)
    original_mtime = source.stat().st_mtime_ns
    source.write_bytes(b"B" * 200_000)
    os.utime(source, ns=(original_mtime, original_mtime))

    assert not source_snapshot_matches(source, snapshot)
