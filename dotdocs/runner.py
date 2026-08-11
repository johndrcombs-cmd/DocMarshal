from __future__ import annotations

import csv
import json
from pathlib import Path

from .processor import analyze_pdf

CSV_FIELDS = (
    "source_file",
    "status",
    "reasons",
    "unit",
    "asset_owner",
    "document_type",
    "controlling_date",
    "proposed_filename",
    "proposed_destination",
)


def process_inbox(
    incoming_folder: str | Path,
    review_folder: str | Path,
    database_path: str | Path,
    unit_folders_root: str | Path,
    *,
    farm_asset_folders_root: str | Path | None = None,
    report_name: str = "review_report",
) -> dict:
    incoming_folder = Path(incoming_folder)
    review_folder = Path(review_folder)
    review_folder.mkdir(parents=True, exist_ok=True)

    results = [
        analyze_pdf(
            pdf_path,
            database_path,
            unit_folders_root,
            farm_asset_folders_root=farm_asset_folders_root,
        )
        for pdf_path in sorted(incoming_folder.glob("*.pdf"), key=lambda path: path.name.lower())
    ]

    json_path = review_folder / f"{report_name}.json"
    csv_path = review_folder / f"{report_name}.csv"
    json_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in results:
            row = {field: result.get(field) for field in CSV_FIELDS}
            row["reasons"] = ";".join(result.get("reasons", []))
            writer.writerow(row)

    return {
        "files_scanned": len(results),
        "ready_for_review": sum(item["status"] == "ready_for_review" for item in results),
        "needs_review": sum(item["status"] == "needs_review" for item in results),
        "json_report": str(json_path),
        "csv_report": str(csv_path),
    }
