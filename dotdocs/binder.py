from __future__ import annotations

from collections import Counter
from contextlib import closing
from pathlib import Path
import sqlite3

import fitz


BINDER_SECTIONS = (
    ("Annual DOT", "001_Annual_DOT"),
    ("Insurance", "002_Insurance"),
    ("Registration", "003_Registration"),
    ("Maintenance", "004_Maintenance_Records"),
)
_SECTION_FOLDERS = {folder for _label, folder in BINDER_SECTIONS}


def advance_binder_position(
    page_counts_by_section: tuple[tuple[int, ...], ...] | list,
    section_index: int,
    document_index: int | None,
    page_index: int | None,
    direction: int,
) -> tuple[int, int | None, int | None]:
    if direction not in {-1, 1}:
        raise ValueError("Binder navigation direction must be previous or next.")
    if not 0 <= section_index < len(page_counts_by_section):
        raise IndexError("Binder section is outside the configured categories.")
    documents = page_counts_by_section[section_index]
    if direction > 0:
        if document_index is not None and page_index is not None:
            if page_index + 1 < documents[document_index]:
                return section_index, document_index, page_index + 1
            if document_index + 1 < len(documents):
                return section_index, document_index + 1, 0
        next_section = section_index + 1
        if next_section >= len(page_counts_by_section):
            return section_index, document_index, page_index
        next_documents = page_counts_by_section[next_section]
        return (next_section, 0, 0) if next_documents else (next_section, None, None)

    if document_index is not None and page_index is not None:
        if page_index > 0:
            return section_index, document_index, page_index - 1
        if document_index > 0:
            previous_document = document_index - 1
            return section_index, previous_document, page_counts_by_section[section_index][previous_document] - 1
    previous_section = section_index - 1
    if previous_section < 0:
        return section_index, document_index, page_index
    previous_documents = page_counts_by_section[previous_section]
    if not previous_documents:
        return previous_section, None, None
    previous_document = len(previous_documents) - 1
    return previous_section, previous_document, previous_documents[previous_document] - 1


def list_binders(
    database_path: str | Path,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path,
) -> list[dict]:
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            "SELECT normalized_unit, asset_owner FROM units WHERE normalized_unit <> ''"
        ).fetchall()
    counts = Counter(row[0] for row in rows)
    company_root = Path(unit_folders_root)
    farm_root = Path(farm_asset_folders_root)
    binders = []
    for unit, owner in rows:
        if counts[unit] != 1:
            continue
        root = farm_root if owner == "Farm Asset" else company_root
        folder = root / f"Unit_{unit}"
        binders.append(
            {
                "unit": unit,
                "owner": owner or "",
                "folder": folder,
                "available": folder.is_dir() and folder.resolve().parent == root.resolve(),
            }
        )
    return sorted(binders, key=lambda item: (len(item["unit"]), item["unit"]))


def list_binder_documents(unit_folder: str | Path, section_folder: str) -> list[Path]:
    unit = Path(unit_folder)
    if section_folder not in _SECTION_FOLDERS:
        raise ValueError("Choose a canonical binder section.")
    section = unit / section_folder
    if not unit.is_dir() or not section.is_dir():
        return []
    if section.resolve().parent != unit.resolve():
        raise ValueError("The binder section resolves outside the selected unit folder.")
    documents = [
        path
        for path in section.iterdir()
        if path.is_file()
        and path.suffix.lower() == ".pdf"
        and path.resolve().parent == section.resolve()
    ]
    return sorted(documents, key=lambda path: path.name.casefold())


def render_binder_page(
    pdf_path: str | Path,
    expected_folder: str | Path,
    page_index: int,
    *,
    max_width: int = 900,
    max_height: int = 700,
    zoom_factor: float = 1.0,
) -> dict:
    path = Path(pdf_path)
    folder = Path(expected_folder)
    if path.suffix.lower() != ".pdf" or path.resolve().parent != folder.resolve():
        raise ValueError("The selected PDF is not directly inside the active binder section.")
    if not path.is_file():
        raise ValueError("The selected binder PDF is unavailable.")
    if max_width < 1 or max_height < 1:
        raise ValueError("The page rendering area is invalid.")
    if not 0.25 <= zoom_factor <= 4.0:
        raise ValueError("The binder zoom must be between 25% and 400%.")
    with fitz.open(path) as document:
        page_count = document.page_count
        if page_index < 0 or page_index >= page_count:
            raise IndexError("The requested PDF page is outside the document.")
        page = document.load_page(page_index)
        zoom = min(max_width / page.rect.width, max_height / page.rect.height, 2.0) * zoom_factor
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png = pixmap.tobytes("png")
    return {
        "png": png,
        "page_index": page_index,
        "page_count": page_count,
        "width": pixmap.width,
        "height": pixmap.height,
    }
