from __future__ import annotations

from pathlib import Path
import hashlib

import fitz

from .assets import asset_folder_root
from .analysis import classify_document, extract_controlling_date
from .database import find_asset_owner
from .matching import match_units_in_text
from .naming import build_filename, destination_subfolder
from .normalization import normalize_unit


def extract_pdf_text(pdf_path: str | Path) -> str:
    with fitz.open(pdf_path) as document:
        return "\n".join(page.get_text("text") for page in document).strip()


def source_fingerprint(pdf_path: str | Path) -> tuple[str, int]:
    path = Path(pdf_path)
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def find_unit_folder(unit_folders_root: str | Path, unit: str) -> Path | None:
    root = Path(unit_folders_root)
    normalized = normalize_unit(unit)
    if not normalized or not root.is_dir():
        return None
    candidate = root / f"Unit_{normalized}"
    if not candidate.is_dir():
        return None
    try:
        if candidate.resolve().parent != root.resolve():
            return None
    except OSError:
        return None
    return candidate


def analyze_pdf(
    pdf_path: str | Path,
    database_path: str | Path,
    unit_folders_root: str | Path,
    *,
    farm_asset_folders_root: str | Path | None = None,
) -> dict:
    pdf_path = Path(pdf_path)
    text = extract_pdf_text(pdf_path)
    source_sha256, source_size = source_fingerprint(pdf_path)
    result = {
        "source_file": str(pdf_path),
        "status": "needs_review",
        "reasons": [],
        "unit": None,
        "asset_owner": None,
        "document_type": None,
        "controlling_date": None,
        "page_suffix": None,
        "proposed_filename": None,
        "proposed_destination": None,
        "source_sha256": source_sha256,
        "source_size": source_size,
    }
    if not text:
        result["reasons"].append("NO_SEARCHABLE_TEXT")
        return result

    match = match_units_in_text(database_path, text)
    if match["status"] != "unique":
        result["reasons"].append("UNIT_" + match["status"].upper())
    else:
        result["unit"] = match["units"][0]
        result["asset_owner"] = find_asset_owner(database_path, result["unit"])

    document_type = classify_document(text)
    if document_type is None:
        result["reasons"].append("DOCUMENT_TYPE_UNKNOWN")
    else:
        result["document_type"] = document_type

    controlling_date = extract_controlling_date(text, document_type) if document_type else None
    if controlling_date is None:
        result["reasons"].append("CONTROLLING_DATE_UNKNOWN")
    else:
        result["controlling_date"] = controlling_date.isoformat()

    selected_root = asset_folder_root(
        result["asset_owner"], unit_folders_root, farm_asset_folders_root
    )
    unit_folder = find_unit_folder(selected_root, result["unit"]) if result["unit"] else None
    if result["unit"] and unit_folder is None:
        result["reasons"].append("UNIT_FOLDER_NOT_FOUND")

    if not result["reasons"]:
        filename = build_filename(result["unit"], document_type, controlling_date)
        destination_folder = unit_folder / destination_subfolder(document_type)
        result["proposed_filename"] = filename
        result["proposed_destination"] = str(destination_folder / filename)
        if (destination_folder / filename).exists():
            result["reasons"].append("DESTINATION_ALREADY_EXISTS")
        else:
            result["status"] = "ready_for_review"

    return result
