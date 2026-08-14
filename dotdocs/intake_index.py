from __future__ import annotations

import hashlib
from pathlib import Path


_QUICK_SAMPLE_BYTES = 64 * 1024


def _quick_signature(path: Path, size: int) -> str:
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as handle:
        digest.update(handle.read(_QUICK_SAMPLE_BYTES))
        if size > _QUICK_SAMPLE_BYTES:
            handle.seek(max(0, size - _QUICK_SAMPLE_BYTES))
            digest.update(handle.read(_QUICK_SAMPLE_BYTES))
    return digest.hexdigest()


def source_snapshot(path: str | Path) -> dict:
    source = Path(path)
    before = source.stat()
    signature = _quick_signature(source, before.st_size)
    after = source.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise OSError(f"The source changed while its intake snapshot was being read: {source}")
    return {
        "source_size": after.st_size,
        "source_mtime_ns": after.st_mtime_ns,
        "source_quick_signature": signature,
    }


def source_snapshot_matches(path: str | Path, result: dict) -> bool:
    expected_size = result.get("source_size")
    expected_mtime = result.get("source_mtime_ns")
    expected_signature = result.get("source_quick_signature")
    if expected_size is None or expected_mtime is None or not expected_signature:
        return False
    try:
        source = Path(path)
        current = source.stat()
        if current.st_size != int(expected_size) or current.st_mtime_ns != int(expected_mtime):
            return False
        return _quick_signature(source, current.st_size) == expected_signature
    except (OSError, TypeError, ValueError):
        return False
