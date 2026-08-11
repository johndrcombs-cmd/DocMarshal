from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Callable

from .processor import analyze_pdf

EVALUATED_STATUSES = {"approved", "duplicate"}
REVIEW_FIELDS = ("document_type", "unit", "controlling_date")


def _reviewed_pdf(result: dict) -> Path | None:
    status = result.get("status")
    candidates = []
    if status == "duplicate":
        candidates.append(result.get("duplicate_archived_file"))
    elif status == "not_dot":
        candidates.append(result.get("not_dot_archived_file"))
    candidates.append(result.get("source_file"))
    for value in candidates:
        if value and Path(value).is_file():
            return Path(value)
    return None


def _fingerprint(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _field_quality(rows: list[dict], field: str) -> dict[str, int]:
    return {
        "correct": sum(row["prediction"].get(field) == row["label"].get(field) for row in rows),
        "missing": sum(row["prediction"].get(field) in {None, ""} for row in rows),
        "wrong_nonmissing": sum(
            row["prediction"].get(field) not in {None, ""}
            and row["prediction"].get(field) != row["label"].get(field)
            for row in rows
        ),
        "total": len(rows),
    }


def evaluate_review_results(
    results: list[dict],
    *,
    analyzer: Callable[[Path], dict],
) -> dict:
    status_counts = dict(sorted(collections.Counter(result.get("status") for result in results).items()))
    labels_by_sha256 = {}
    fingerprint_errors = []
    analysis_errors = []
    evaluated = []
    not_dot_predictions = {}

    for result in results:
        expected_hash = result.get("source_sha256")
        label = {
            "status": result.get("status"),
            "unit": result.get("unit"),
            "document_type": result.get("document_type"),
            "controlling_date": result.get("controlling_date"),
            "page_suffix": result.get("page_suffix"),
        }
        if expected_hash:
            labels_by_sha256[expected_hash] = label

        path = _reviewed_pdf(result)
        if path is None:
            fingerprint_errors.append(
                {"source_sha256": expected_hash, "reason": "REVIEWED_PDF_UNAVAILABLE"}
            )
            continue
        actual_hash, actual_size = _fingerprint(path)
        if (
            not expected_hash
            or actual_hash != expected_hash
            or result.get("source_size") is None
            or actual_size != result.get("source_size")
        ):
            fingerprint_errors.append(
                {"source_sha256": expected_hash, "reason": "FILE_FINGERPRINT_MISMATCH"}
            )
            continue

        try:
            prediction = analyzer(path)
        except Exception as error:
            analysis_errors.append(
                {"source_sha256": expected_hash, "reason": str(error)}
            )
            continue

        status = result.get("status")
        if status == "not_dot":
            not_dot_predictions[expected_hash] = {
                field: prediction.get(field) for field in REVIEW_FIELDS
            }
        elif status in EVALUATED_STATUSES:
            evaluated.append(
                {
                    "source_sha256": expected_hash,
                    "label": label,
                    "prediction": prediction,
                }
            )

    mismatch_by_sha256 = {}
    for row in evaluated:
        changed = {
            field: {
                "expected": row["label"].get(field),
                "predicted": row["prediction"].get(field),
            }
            for field in REVIEW_FIELDS
            if row["prediction"].get(field) != row["label"].get(field)
        }
        if changed:
            mismatch_by_sha256[row["source_sha256"]] = changed

    return {
        "schema_version": 1,
        "document_count": len(results),
        "status_counts": status_counts,
        "eligible_documents": len(evaluated),
        "all_fields_correct": sum(
            all(row["prediction"].get(field) == row["label"].get(field) for field in REVIEW_FIELDS)
            for row in evaluated
        ),
        "field_quality": {
            field: _field_quality(evaluated, field) for field in REVIEW_FIELDS
        },
        "labels_by_sha256": labels_by_sha256,
        "mismatches_by_sha256": mismatch_by_sha256,
        "not_dot_predictions_by_sha256": not_dot_predictions,
        "fingerprint_errors": fingerprint_errors,
        "analysis_errors": analysis_errors,
    }


def evaluate_review_session(
    session_path: str | Path,
    *,
    database_path: str | Path,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path | None = None,
) -> dict:
    results = json.loads(Path(session_path).read_text(encoding="utf-8"))
    if not isinstance(results, list) or not all(isinstance(result, dict) for result in results):
        raise ValueError("The review session must contain a JSON list of document records.")

    def analyzer(path: Path) -> dict:
        return analyze_pdf(
            path,
            database_path,
            unit_folders_root,
            farm_asset_folders_root=farm_asset_folders_root,
        )

    return evaluate_review_results(results, analyzer=analyzer)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate the current document analyzer against fingerprinted reviewer outcomes."
    )
    parser.add_argument("--config", default="config.json", help="Path to the application config JSON.")
    parser.add_argument("--session", help="Review session JSON; defaults to active_review.json from config.")
    parser.add_argument("--output", help="Optional destination JSON report.")
    args = parser.parse_args(argv)

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    session_path = Path(args.session) if args.session else Path(config["scan_review"]) / "active_review.json"
    report = evaluate_review_session(
        session_path,
        database_path=config["fleet_database"],
        unit_folders_root=config["unit_folders_root"],
        farm_asset_folders_root=config.get("farm_asset_folders_root"),
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(output)
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
