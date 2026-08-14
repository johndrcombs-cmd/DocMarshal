from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import fitz

from .review import _file_identity, _lock_verified_owned_file, _remove_owned_file_via_quarantine


DEFAULT_NAPS2_CONSOLE = Path(r"C:\Program Files\NAPS2\NAPS2.Console.exe")
DEFAULT_SCANNER_DEVICE = "EPSON ES-400II"
SCAN_MODE_COMBINED = "One combined PDF"
SCAN_MODE_EACH_PAGE = "Each page separate"
SCAN_MODE_BLANK_SEPARATORS = "Split at blank separator pages"
SCAN_MODES = (SCAN_MODE_COMBINED, SCAN_MODE_EACH_PAGE, SCAN_MODE_BLANK_SEPARATORS)


class ScannerError(RuntimeError):
    pass


def _fingerprint(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def find_naps2_console() -> Path:
    candidates = (
        DEFAULT_NAPS2_CONSOLE,
        Path(r"C:\Program Files (x86)\NAPS2\NAPS2.Console.exe"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    discovered = shutil.which("NAPS2.Console.exe") or shutil.which("naps2.console.exe")
    if discovered:
        return Path(discovered)
    raise ScannerError("NAPS2 Console is not installed or could not be found.")


def build_scan_command(executable: str | Path, output: str | Path) -> list[str]:
    return [
        str(executable),
        "--noprofile",
        "--driver", "twain",
        "--device", DEFAULT_SCANNER_DEVICE,
        "--source", "duplex",
        "--pagesize", "letter",
        "--dpi", "600",
        "--bitdepth", "color",
        "--deskew",
        "--enableocr",
        "--ocrlang", "eng",
        "--output", str(output),
    ]


def _validate_scanned_pdf(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise ScannerError("The scanner did not produce a PDF.")
    try:
        # Validate from memory so a failed MuPDF open cannot leave the staging
        # file locked on Windows and prevent cleanup.
        with fitz.open(stream=path.read_bytes(), filetype="pdf") as document:
            if document.page_count < 1:
                raise ScannerError("The scanner produced an empty PDF.")
            document.load_page(0)
    except ScannerError:
        raise
    except Exception as error:
        raise ScannerError("The scanner output is not a valid PDF.") from error


def _unique_destination(incoming: Path, stem: str) -> Path:
    base = incoming / f"{stem}.pdf"
    destination = base
    sequence = 2
    while destination.exists():
        destination = incoming / f"{base.stem}_{sequence}.pdf"
        sequence += 1
    return destination


def _move_to_unique(source: Path, incoming: Path, stem: str) -> Path:
    while True:
        destination = _unique_destination(incoming, stem)
        try:
            source.rename(destination)
        except FileExistsError:
            continue
        return destination


def _rollback_published_file(
    destination: Path,
    identity: os.stat_result,
    expected_fingerprint: tuple[str, int],
) -> str | None:
    try:
        _remove_owned_file_via_quarantine(
            destination,
            identity,
            expected_fingerprint[0],
            expected_fingerprint[1],
        )
        return None
    except Exception as error:
        return str(error)


def _page_is_blank(page: fitz.Page) -> bool:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
    samples = memoryview(pixmap.samples)
    dark_pixels = sum(value < 245 for value in samples)
    return dark_pixels / max(1, len(samples)) < 0.005


def _page_groups(source: Path, mode: str) -> list[list[int]]:
    with fitz.open(source) as document:
        if mode == SCAN_MODE_COMBINED:
            return [list(range(document.page_count))]
        if mode == SCAN_MODE_EACH_PAGE:
            return [[index] for index in range(document.page_count)]
        groups: list[list[int]] = []
        current: list[int] = []
        for index, page in enumerate(document):
            if _page_is_blank(page):
                if current:
                    groups.append(current)
                    current = []
            else:
                current.append(index)
        if current:
            groups.append(current)
        if not groups:
            raise ScannerError("All scanned pages were detected as blank separator pages.")
        return groups


def _write_page_group(source: Path, page_indexes: list[int], output: Path) -> None:
    with fitz.open(source) as original, fitz.open() as split:
        for page_index in page_indexes:
            split.insert_pdf(original, from_page=page_index, to_page=page_index)
        split.save(output)


def _publish_scan_outputs(source: Path, incoming: Path, timestamp: str, mode: str, staging: Path) -> list[Path]:
    groups = _page_groups(source, mode)
    if mode == SCAN_MODE_COMBINED:
        expected_fingerprint = _fingerprint(source)
        expected_identity = _file_identity(source)
        destination = _move_to_unique(source, incoming, f"DocMarshal_Scan_{timestamp}")
        try:
            with _lock_verified_owned_file(
                destination,
                expected_identity,
                expected_fingerprint[0],
                expected_fingerprint[1],
            ):
                return [destination]
        except Exception as error:
            rollback_error = _rollback_published_file(
                destination,
                expected_identity,
                expected_fingerprint,
            )
            if rollback_error:
                raise ScannerError(f"{error}; {rollback_error}") from error
            raise

    staged_outputs: list[Path] = []
    published: list[tuple[Path, os.stat_result, tuple[str, int]]] = []
    try:
        for sequence, pages in enumerate(groups, start=1):
            staged = staging / f"split-{sequence:03d}.pdf"
            _write_page_group(source, pages, staged)
            _validate_scanned_pdf(staged)
            staged_outputs.append(staged)
        with ExitStack() as publication_locks:
            for sequence, staged in enumerate(staged_outputs, start=1):
                expected_fingerprint = _fingerprint(staged)
                expected_identity = _file_identity(staged)
                destination = _move_to_unique(
                    staged,
                    incoming,
                    f"DocMarshal_Scan_{timestamp}_{sequence:03d}",
                )
                published.append((destination, expected_identity, expected_fingerprint))
                publication_locks.enter_context(
                    _lock_verified_owned_file(
                        destination,
                        expected_identity,
                        expected_fingerprint[0],
                        expected_fingerprint[1],
                    )
                )
            return [destination for destination, _identity, _fingerprint_value in published]
    except Exception as error:
        rollback_errors: list[str] = []
        for destination, expected_identity, expected_fingerprint in published:
            try:
                rollback_error = _rollback_published_file(
                    destination,
                    expected_identity,
                    expected_fingerprint,
                )
                if rollback_error:
                    rollback_errors.append(rollback_error)
            except OSError as rollback_error:
                rollback_errors.append(f"rollback failed for {destination}: {rollback_error}")
        if rollback_errors:
            raise ScannerError(f"{error}; " + "; ".join(rollback_errors)) from error
        raise
    finally:
        for staged in staged_outputs:
            staged.unlink(missing_ok=True)


def scan_to_incoming(
    incoming_folder: str | Path,
    *,
    executable: str | Path | None = None,
    timestamp: str | None = None,
    mode: str = SCAN_MODE_COMBINED,
) -> list[Path]:
    incoming = Path(incoming_folder)
    if not incoming.is_dir():
        raise ScannerError(f"The Incoming folder is unavailable: {incoming}")
    if mode not in SCAN_MODES:
        raise ScannerError(f"Unsupported scan mode: {mode}")
    console = Path(executable) if executable is not None else find_naps2_console()
    if not console.is_file():
        raise ScannerError(f"NAPS2 Console is unavailable: {console}")

    staging = incoming / ".docmarshal-scan-staging"
    staging.mkdir(exist_ok=True)
    temporary: Path | None = None
    try:
        handle, temporary_name = tempfile.mkstemp(prefix="scan-", suffix=".pdf", dir=staging)
        os.close(handle)
        temporary = Path(temporary_name)
        temporary.unlink()
        command = build_scan_command(console, temporary)
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=1800,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "Unknown scanner error").strip()
            raise ScannerError(f"Scanning failed: {detail}")
        _validate_scanned_pdf(temporary)
        outputs = _publish_scan_outputs(
            temporary,
            incoming,
            timestamp or datetime.now().strftime("%Y%m%d_%H%M%S"),
            mode,
            staging,
        )
        if mode == SCAN_MODE_COMBINED:
            temporary = None
        return outputs
    except subprocess.TimeoutExpired as error:
        raise ScannerError("Scanning timed out before the PDF was completed.") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        try:
            staging.rmdir()
        except OSError:
            pass
