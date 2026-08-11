from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Iterable

import fitz


class DocumentImportError(RuntimeError):
    pass


class OCRUnavailableError(DocumentImportError):
    pass


_OCR_REASON_CODES = {"NO_SEARCHABLE_TEXT", "PDF_REQUIRES_OCR"}


def ocr_candidate_paths(results: Iterable[dict], incoming_root: str | Path) -> list[Path]:
    incoming = Path(incoming_root).resolve()
    candidates: dict[Path, Path] = {}
    for result in results:
        reasons = {str(reason).strip().upper() for reason in result.get("reasons", ())}
        if not reasons.intersection(_OCR_REASON_CODES):
            continue
        source = Path(result.get("source_file", ""))
        if source.suffix.lower() != ".pdf" or not source.is_file():
            continue
        resolved = source.resolve()
        if resolved.parent != incoming:
            continue
        candidates[resolved] = source
    return sorted(candidates.values(), key=lambda path: path.name.casefold())


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_pdf_documents(sources: Iterable[str | Path], incoming_root: str | Path) -> list[dict]:
    incoming = Path(incoming_root)
    if not incoming.is_dir():
        raise DocumentImportError(f"The Incoming folder is unavailable: {incoming}")
    incoming_resolved = incoming.resolve()
    results: list[dict] = []
    for source_value in sources:
        source = Path(source_value)
        item = {"source": str(source), "filename": source.name}
        if source.suffix.lower() != ".pdf":
            results.append({**item, "status": "unsupported"})
            continue
        if not source.is_file():
            results.append({**item, "status": "source_missing"})
            continue
        destination = incoming / source.name
        if destination.resolve().parent != incoming_resolved:
            results.append({**item, "status": "unsafe_destination"})
            continue
        source_hash = _sha256(source)
        source_size = source.stat().st_size
        if destination.exists():
            if destination.is_file() and destination.stat().st_size == source_size and _sha256(destination) == source_hash:
                results.append({**item, "destination": str(destination), "status": "already_identical", "sha256": source_hash})
            else:
                results.append({**item, "destination": str(destination), "status": "destination_conflict", "sha256": source_hash})
            continue
        try:
            with source.open("rb") as incoming_handle, destination.open("xb") as outgoing_handle:
                shutil.copyfileobj(incoming_handle, outgoing_handle, length=1024 * 1024)
                outgoing_handle.flush()
                os.fsync(outgoing_handle.fileno())
            shutil.copystat(source, destination)
            if destination.stat().st_size != source_size or _sha256(destination) != source_hash:
                destination.unlink(missing_ok=True)
                raise DocumentImportError(f"Imported PDF verification failed: {source.name}")
        except FileExistsError:
            results.append({**item, "destination": str(destination), "status": "destination_conflict", "sha256": source_hash})
            continue
        results.append({**item, "destination": str(destination), "status": "copied_verified", "sha256": source_hash})
    return results


def pdf_has_searchable_text(pdf_path: str | Path) -> bool:
    path = Path(pdf_path)
    if path.suffix.lower() != ".pdf" or not path.is_file():
        raise DocumentImportError(f"The selected PDF is unavailable: {path}")
    with fitz.open(path) as document:
        return any(page.get_text("text").strip() for page in document)


def find_tesseract(executable: str | Path | None = None) -> Path:
    candidates = []
    if executable:
        candidates.append(Path(executable))
    discovered = shutil.which("tesseract")
    if discovered:
        candidates.append(Path(discovered))
    candidates.extend(
        (
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Tesseract-OCR" / "tesseract.exe",
            Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
            Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise OCRUnavailableError(
        "Tesseract OCR is not installed. Run Setup DocMarshal again to install the OCR component."
    )


def _backup_destination(source: Path, backup_root: Path) -> Path:
    candidate = backup_root / source.name
    index = 2
    while candidate.exists():
        if candidate.is_file() and candidate.stat().st_size == source.stat().st_size and _sha256(candidate) == _sha256(source):
            return candidate
        candidate = backup_root / f"{source.stem}_{index}{source.suffix}"
        index += 1
    return candidate


def run_pdf_ocr(
    pdf_path: str | Path,
    *,
    incoming_root: str | Path,
    backup_root: str | Path,
    page_ocr: Callable[[Path, Path], None] | None = None,
    tesseract_executable: str | Path | None = None,
) -> dict:
    source = Path(pdf_path)
    incoming = Path(incoming_root)
    if source.suffix.lower() != ".pdf" or not source.is_file():
        raise DocumentImportError(f"The selected PDF is unavailable: {source}")
    if source.resolve().parent != incoming.resolve():
        raise DocumentImportError("OCR is restricted to PDFs directly inside the Incoming folder.")
    if pdf_has_searchable_text(source):
        return {"status": "already_searchable", "source": str(source), "backup_path": None}

    if page_ocr is None:
        tesseract = find_tesseract(tesseract_executable)

        def page_ocr(image_path: Path, output_pdf: Path) -> None:
            completed = subprocess.run(
                (str(tesseract), str(image_path), str(output_pdf.with_suffix("")), "pdf"),
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if completed.returncode != 0 or not output_pdf.is_file():
                detail = completed.stderr.strip() or completed.stdout.strip() or "Tesseract did not create an output PDF."
                raise DocumentImportError(f"OCR failed for {image_path.name}: {detail}")

    original_size = source.stat().st_size
    original_hash = _sha256(source)
    with tempfile.TemporaryDirectory(prefix=".docmarshal-ocr-", dir=incoming) as temporary_name:
        temporary = Path(temporary_name)
        with fitz.open(source) as original_document:
            original_pages = original_document.page_count
            if original_pages < 1:
                raise DocumentImportError("The selected PDF has no pages to OCR.")
            page_pdfs = []
            for page_index in range(original_pages):
                page = original_document.load_page(page_index)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
                image_path = temporary / f"page-{page_index + 1:04d}.png"
                output_pdf = temporary / f"page-{page_index + 1:04d}.pdf"
                pixmap.save(image_path)
                page_ocr(image_path, output_pdf)
                if not output_pdf.is_file():
                    raise DocumentImportError(f"OCR did not create page {page_index + 1}.")
                page_pdfs.append(output_pdf)

        combined_path = temporary / "combined.pdf"
        combined = fitz.open()
        try:
            for page_pdf in page_pdfs:
                with fitz.open(page_pdf) as page_document:
                    combined.insert_pdf(page_document)
            combined.save(combined_path, garbage=4, deflate=True)
        finally:
            combined.close()
        with fitz.open(combined_path) as verified:
            if verified.page_count != original_pages:
                raise DocumentImportError("OCR output page count does not match the source PDF.")
            if not any(page.get_text("text").strip() for page in verified):
                raise DocumentImportError("OCR completed without producing searchable text.")

        if source.stat().st_size != original_size or _sha256(source) != original_hash:
            raise DocumentImportError("The Incoming PDF changed while OCR was running; no replacement was made.")
        backup = Path(backup_root)
        backup.mkdir(parents=True, exist_ok=True)
        backup_path = _backup_destination(source, backup)
        if not backup_path.exists():
            with source.open("rb") as incoming_handle, backup_path.open("xb") as outgoing_handle:
                shutil.copyfileobj(incoming_handle, outgoing_handle, length=1024 * 1024)
                outgoing_handle.flush()
                os.fsync(outgoing_handle.fileno())
            shutil.copystat(source, backup_path)
        if backup_path.stat().st_size != original_size or _sha256(backup_path) != original_hash:
            raise DocumentImportError("The OCR original backup could not be verified.")
        os.replace(combined_path, source)

    if not pdf_has_searchable_text(source):
        raise DocumentImportError("The published OCR PDF is not searchable.")
    return {
        "status": "ocr_completed",
        "source": str(source),
        "backup_path": str(backup_path),
        "page_count": original_pages,
        "sha256_before": original_hash,
        "sha256_after": _sha256(source),
    }
