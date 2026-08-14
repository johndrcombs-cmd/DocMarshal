from __future__ import annotations

import json
import shutil
import hashlib
import ctypes
import os
import re
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from ctypes import wintypes

from .assets import AssetValidationError, asset_folder_root, ensure_standard_unit_folder
from .binder import TOOL_BINDER_SECTIONS
from .database import find_asset_owner
from .naming import DOCUMENT_TYPE_CHOICES, build_filename, build_tool_filename, destination_subfolder
from .normalization import normalize_unit
from .processor import find_unit_folder
from .tools_database import add_tool_certification, get_tool_by_identifier, remove_tool_certification

ALLOWED_DOCUMENT_TYPES = set(DOCUMENT_TYPE_CHOICES)
DOCUMENT_TYPE_ERROR = "Choose a valid document type: " + ", ".join(DOCUMENT_TYPE_CHOICES) + "."
NON_DOT_DOCUMENT_TYPES = {
    "MVR_AUTH": "MVR Auth",
    "CALIBRATION_CERT": "Calibration Certificate",
    "TRAINING_DOC": "Training Document",
    "OTHER": "Other / Unclassified",
}


class ReviewValidationError(ValueError):
    pass


class ApprovalError(RuntimeError):
    pass


class _FILE_DISPOSITION_INFO(ctypes.Structure):
    _fields_ = [("DeleteFile", wintypes.BOOLEAN)]


def _win32_open_osfhandle(handle: int) -> int:
    import msvcrt

    return msvcrt.open_osfhandle(handle, os.O_RDONLY)


def _fdopen_binary_read(descriptor: int):
    return os.fdopen(descriptor, "rb", closefd=True)


def _file_identity(path: Path) -> os.stat_result:
    return path.stat()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _matches_owned_file(
    path: Path,
    identity: os.stat_result,
    expected_hash: str,
    expected_size: int,
) -> bool:
    try:
        current = path.stat()
        if not os.path.samestat(identity, current) or current.st_size != expected_size:
            return False
        return _file_sha256(path) == expected_hash
    except (FileNotFoundError, OSError):
        return False


def _delete_owned_quarantine_windows(
    path: Path,
    identity: os.stat_result,
    expected_hash: str,
    expected_size: int,
) -> bool:
    import msvcrt

    generic_read = 0x80000000
    delete_access = 0x00010000
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_disposition_info = 4
    invalid_handle_value = ctypes.c_void_p(-1).value

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.CreateFileW(
        str(path),
        generic_read | delete_access,
        0,
        None,
        open_existing,
        file_attribute_normal,
        None,
    )
    if handle == invalid_handle_value:
        raise OSError(ctypes.get_last_error(), f"Could not exclusively open rollback quarantine: {path}")

    try:
        descriptor = _win32_open_osfhandle(handle)
    except Exception:
        kernel32.CloseHandle(handle)
        raise
    try:
        quarantine_file = _fdopen_binary_read(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    with quarantine_file:
        current = os.fstat(quarantine_file.fileno())
        digest = hashlib.sha256()
        for chunk in iter(lambda: quarantine_file.read(1024 * 1024), b""):
            digest.update(chunk)
        if (
            not os.path.samestat(identity, current)
            or current.st_size != expected_size
            or digest.hexdigest() != expected_hash
        ):
            return False
        disposition = _FILE_DISPOSITION_INFO(True)
        if not kernel32.SetFileInformationByHandle(
            msvcrt.get_osfhandle(quarantine_file.fileno()),
            file_disposition_info,
            ctypes.byref(disposition),
            ctypes.sizeof(disposition),
        ):
            raise OSError(ctypes.get_last_error(), f"Could not mark rollback quarantine for deletion: {path}")
    return True


def _remove_owned_file_via_quarantine(
    path: Path,
    identity: os.stat_result,
    expected_hash: str,
    expected_size: int,
) -> None:
    quarantine = path.with_name(f".{path.name}.docmarshal-rollback-{uuid.uuid4().hex}.tmp")
    try:
        path.rename(quarantine)
    except FileNotFoundError:
        return
    if os.name == "nt":
        owned_removed = _delete_owned_quarantine_windows(
            quarantine, identity, expected_hash, expected_size
        )
    elif _matches_owned_file(quarantine, identity, expected_hash, expected_size):
        quarantine.unlink()
        owned_removed = True
    else:
        owned_removed = False
    if owned_removed:
        return
    if path.exists():
        raise ApprovalError(
            "A changed production file was quarantined during rollback, but its original path is now occupied; "
            f"contact IT. Quarantine: {quarantine}"
        )
    quarantine.rename(path)
    raise ApprovalError(f"Refusing to remove a production file that changed during approval: {path}")


def _parse_review_date(value: str) -> date:
    value = value.strip()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        pass

    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{2}|\d{4})", value)
    if match:
        month, day, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        try:
            return date(year, month, day)
        except ValueError:
            pass
    raise ReviewValidationError(
        "Enter a date such as 8/8/26, 08/08/2026, or 2026-08-08."
    )


def _normalize_page_suffix(value: str | None) -> str | None:
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    match = re.fullmatch(r"PG([2-9]\d*)", normalized)
    if match is None:
        raise ReviewValidationError("Enter an additional page as PG2, PG3, PG4, and so on.")
    return normalized


def apply_correction(
    result: dict,
    *,
    unit: str,
    document_type: str,
    controlling_date: str,
    page_suffix: str | None = None,
    unit_folders_root: str | Path,
    farm_asset_folders_root: str | Path | None = None,
    tool_folders_root: str | Path | None = None,
    database_path: str | Path | None = None,
    audit_path: str | Path | None = None,
) -> dict:
    normalized_type = document_type.strip().upper()
    if normalized_type not in ALLOWED_DOCUMENT_TYPES:
        raise ReviewValidationError(DOCUMENT_TYPE_ERROR)

    parsed_date = _parse_review_date(controlling_date)
    normalized_page_suffix = _normalize_page_suffix(page_suffix)
    is_tool = normalized_type == "CAL" or result.get("subject_type") == "tool"
    tool = None
    if is_tool:
        if normalized_type != "CAL" or database_path is None or tool_folders_root is None:
            raise ReviewValidationError("Calibration documents require the tool database and Tool Binder root.")
        try:
            tool = get_tool_by_identifier(database_path, unit)
        except ValueError as error:
            raise ReviewValidationError(str(error)) from error
        tool_root = Path(tool_folders_root)
        tool_folder = tool_root / f"Tool_{tool['display_tool_id']}"
        if not tool_root.is_dir():
            raise ReviewValidationError(f"The configured Tool Binder root does not exist: {tool_root}")
        if tool_folder.parent.resolve() != tool_root.resolve():
            raise ReviewValidationError("The Tool Binder path is unsafe.")
        tool_folder.mkdir(exist_ok=True)
        for _label, section_folder in TOOL_BINDER_SECTIONS:
            (tool_folder / section_folder).mkdir(exist_ok=True)
        normalized_unit = ""
        trusted_owner = None
        filename = build_tool_filename(tool["display_tool_id"], parsed_date, suffix=normalized_page_suffix)
        destination = tool_folder / destination_subfolder(normalized_type) / filename
    else:
        normalized_unit = normalize_unit(unit)
        if not normalized_unit:
            raise ReviewValidationError("A unit number is required.")
        trusted_owner = find_asset_owner(database_path, normalized_unit) if database_path else result.get("asset_owner")
        try:
            if database_path is not None and audit_path is not None:
                unit_folder, trusted_owner = ensure_standard_unit_folder(
                    database_path=database_path, audit_path=audit_path,
                    unit_folders_root=unit_folders_root,
                    farm_asset_folders_root=farm_asset_folders_root, unit=normalized_unit,
                )
            else:
                selected_root = asset_folder_root(trusted_owner, unit_folders_root, farm_asset_folders_root)
                unit_folder = find_unit_folder(selected_root, normalized_unit)
        except AssetValidationError as error:
            raise ReviewValidationError(str(error)) from error
        if unit_folder is None:
            raise ReviewValidationError(f"No production folder was found for unit {normalized_unit}.")
        filename = build_filename(normalized_unit, normalized_type, parsed_date, suffix=normalized_page_suffix)
        destination = unit_folder / destination_subfolder(normalized_type) / filename
    if not destination.parent.is_dir():
        raise ReviewValidationError(f"The destination folder does not exist: {destination.parent}")
    if destination.exists():
        raise ReviewValidationError(f"The destination file already exists: {destination}")

    review_fields_changed = any(
        (
            (str(result.get("subject_id") or "") != tool["display_tool_id"]) if is_tool else (normalize_unit(result.get("unit") or "") != normalized_unit),
            str(result.get("document_type") or "").strip().upper() != normalized_type,
            result.get("controlling_date") != parsed_date.isoformat(),
            result.get("page_suffix") != normalized_page_suffix,
        )
    )

    corrected = dict(result)
    corrected.update(
        {
            "status": "ready_for_review",
            "reasons": [],
            "unit": None if is_tool else normalized_unit,
            "subject_type": "tool" if is_tool else "fleet_unit",
            "subject_id": tool["display_tool_id"] if is_tool else normalized_unit,
            "tool_database_id": tool["id"] if is_tool else None,
            "asset_owner": trusted_owner,
            "document_type": normalized_type,
            "controlling_date": parsed_date.isoformat(),
            "page_suffix": normalized_page_suffix,
            "proposed_filename": filename,
            "proposed_destination": str(destination),
            "manually_corrected": bool(result.get("manually_corrected")) or review_fields_changed,
        }
    )
    return corrected


def _append_audit(audit_path: str | Path, entry: dict) -> None:
    audit_path = Path(audit_path)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    with audit_path.open("a", encoding="utf-8") as audit_file:
        audit_file.write(json.dumps(record, sort_keys=True) + "\n")
        audit_file.flush()
        os.fsync(audit_file.fileno())


def save_review_session(path: str | Path, results: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(path.name + ".tmp")
    temporary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    temporary_path.replace(path)


def load_review_session(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ReviewValidationError("The review session file is not a list of documents.")
    return data


def record_correction(audit_path: str | Path, before: dict, after: dict) -> bool:
    correction_fields = ("unit", "document_type", "controlling_date", "page_suffix")
    if all(before.get(field) == after.get(field) for field in correction_fields):
        return False
    fields = (
        "status",
        "reasons",
        "unit",
        "document_type",
        "controlling_date",
        "page_suffix",
        "proposed_filename",
        "proposed_destination",
    )
    _append_audit(
        audit_path,
        {
            "event": "correction_saved",
            "source_file": after.get("source_file") or before.get("source_file"),
            "before": {field: before.get(field) for field in fields},
            "after": {field: after.get(field) for field in fields},
        },
    )
    return True


def record_asset_created(audit_path: str | Path, asset: dict) -> None:
    _append_audit(
        audit_path,
        {
            "event": "asset_created",
            "unit": asset.get("unit"),
            "asset_owner": asset.get("asset_owner"),
            "plate": asset.get("plate"),
            "vin": asset.get("vin"),
        },
    )


def mark_not_dot_document(
    result: dict,
    *,
    classification: str,
    audit_path: str | Path,
    incoming_folder: str | Path,
    exceptions_folder: str | Path,
) -> dict:
    classification_code = str(classification or "").strip().upper()
    if classification_code not in NON_DOT_DOCUMENT_TYPES:
        raise ReviewValidationError("Choose a valid Not DOT document classification before archiving.")
    classification_label = NON_DOT_DOCUMENT_TYPES[classification_code]
    source = Path(result.get("source_file") or "")
    incoming_folder = Path(incoming_folder).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ReviewValidationError(f"The selected source PDF is unavailable: {source}")
    if source.resolve().parent != incoming_folder:
        raise ReviewValidationError(
            "The selected source is not a PDF directly inside the configured Incoming folder."
        )

    expected_hash = result.get("source_sha256")
    expected_size = result.get("source_size")
    if not expected_hash or expected_size is None:
        raise ReviewValidationError(
            "The document has no review fingerprint; scan it again before removing it from the DOT workflow."
        )
    digest = hashlib.sha256()
    actual_size = 0
    with source.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
            actual_size += len(chunk)
    if digest.hexdigest() != expected_hash or actual_size != expected_size:
        raise ReviewValidationError(
            "The source PDF changed since review; scan it again before removing it from the DOT workflow."
        )

    exceptions_folder = Path(exceptions_folder)
    exceptions_folder.mkdir(parents=True, exist_ok=True)
    not_dot_folder = exceptions_folder / "Not DOT"
    not_dot_folder.mkdir(exist_ok=True)
    if not_dot_folder.resolve().parent != exceptions_folder.resolve():
        raise ReviewValidationError("The configured Not DOT archive folder is unsafe.")
    archive = not_dot_folder / source.name
    sequence = 2
    while archive.exists():
        archive = not_dot_folder / f"{source.stem}_{sequence}{source.suffix}"
        sequence += 1

    started_entry = {
        "source_file": str(source),
        "archived_file": str(archive),
        "non_dot_classification": classification_code,
        "non_dot_classification_label": classification_label,
        "source_sha256": expected_hash,
        "source_size": expected_size,
    }
    _append_audit(audit_path, {"event": "not_dot_mark_started", **started_entry})
    archived = False
    try:
        source.rename(archive)
        archived = True
        _append_audit(audit_path, {"event": "marked_not_dot", **started_entry})
    except Exception as error:
        rollback_error = None
        if archived:
            try:
                archive.rename(source)
            except Exception as rollback_exception:
                rollback_error = rollback_exception
        try:
            _append_audit(
                audit_path,
                {
                    "event": "not_dot_mark_failed",
                    **started_entry,
                    "reason": str(error),
                    "rollback_error": str(rollback_error) if rollback_error else None,
                },
            )
        except Exception:
            pass
        if rollback_error is not None:
            raise ReviewValidationError(
                "The document could not be removed from the DOT workflow and its source PDF could not be restored; contact IT. "
                f"Archive: {archive}. Error: {rollback_error}"
            ) from error
        raise ReviewValidationError(
            f"The document could not be removed from the DOT workflow; the source PDF was left in Incoming: {error}"
        ) from error

    not_dot = dict(result)
    not_dot.update(
        {
            "status": "not_dot",
            "reasons": ["REMOVED_FROM_DOT_WORKFLOW"],
            "proposed_filename": None,
            "proposed_destination": None,
            "not_dot_archived_file": str(archive),
            "non_dot_classification": classification_code,
            "non_dot_classification_label": classification_label,
            "not_dot_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return not_dot


def mark_duplicate_document(
    result: dict,
    *,
    unit: str,
    document_type: str,
    controlling_date: str,
    audit_path: str | Path,
    unit_folders_root: str | Path,
    incoming_folder: str | Path,
    processed_folder: str | Path,
    farm_asset_folders_root: str | Path | None = None,
    database_path: str | Path | None = None,
) -> dict:
    source = Path(result.get("source_file") or "")
    incoming_folder = Path(incoming_folder).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise ReviewValidationError(f"The selected source PDF is unavailable: {source}")
    if source.resolve().parent != incoming_folder:
        raise ReviewValidationError(
            "The selected source is not a PDF directly inside the configured Incoming folder."
        )

    expected_hash = result.get("source_sha256")
    expected_size = result.get("source_size")
    if not expected_hash or expected_size is None:
        raise ReviewValidationError(
            "The document has no review fingerprint; scan it again before marking it duplicate."
        )
    digest = hashlib.sha256()
    actual_size = 0
    with source.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
            actual_size += len(chunk)
    if digest.hexdigest() != expected_hash or actual_size != expected_size:
        raise ReviewValidationError(
            "The source PDF changed since review; scan it again before marking it duplicate."
        )

    processed_folder = Path(processed_folder)
    processed_folder.mkdir(parents=True, exist_ok=True)
    duplicate_folder = processed_folder / "Duplicates"
    duplicate_folder.mkdir(exist_ok=True)
    if duplicate_folder.resolve().parent != processed_folder.resolve():
        raise ReviewValidationError("The configured duplicate archive folder is unsafe.")
    archive = duplicate_folder / source.name
    sequence = 2
    while archive.exists():
        archive = duplicate_folder / f"{source.stem}_duplicate_{sequence}{source.suffix}"
        sequence += 1

    started_entry = {
        "source_file": str(source),
        "archived_file": str(archive),
        "reviewed_subject_type": result.get("subject_type"),
        "reviewed_subject_id": result.get("subject_id") or result.get("unit") or str(unit).strip() or None,
        "reviewed_document_type": result.get("document_type") or str(document_type).strip().upper() or None,
        "reviewed_controlling_date": result.get("controlling_date") or str(controlling_date).strip() or None,
        "source_sha256": expected_hash,
        "source_size": expected_size,
    }
    _append_audit(audit_path, {"event": "duplicate_mark_started", **started_entry})
    archived = False
    try:
        source.rename(archive)
        archived = True
        _append_audit(audit_path, {"event": "marked_duplicate", **started_entry})
    except Exception as error:
        rollback_error = None
        if archived:
            try:
                archive.rename(source)
            except Exception as rollback_exception:
                rollback_error = rollback_exception
        try:
            _append_audit(
                audit_path,
                {
                    "event": "duplicate_mark_failed",
                    **started_entry,
                    "reason": str(error),
                    "rollback_error": str(rollback_error) if rollback_error else None,
                },
            )
        except Exception:
            pass
        if rollback_error is not None:
            raise ReviewValidationError(
                "The duplicate could not be recorded and its source PDF could not be restored; contact IT. "
                f"Archive: {archive}. Error: {rollback_error}"
            ) from error
        raise ReviewValidationError(
            f"The duplicate could not be recorded; the source PDF was left in Incoming: {error}"
        ) from error

    duplicate = dict(result)
    duplicate.update(
        {
            "status": "duplicate",
            "reasons": ["MARKED_DUPLICATE_BY_REVIEWER"],
            "proposed_filename": None,
            "proposed_destination": None,
            "duplicate_destination": None,
            "duplicate_archived_file": str(archive),
            "duplicate_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return duplicate


def restore_archived_document(
    result: dict,
    *,
    audit_path: str | Path,
    incoming_folder: str | Path,
    processed_folder: str | Path,
    exceptions_folder: str | Path,
) -> dict:
    status = result.get("status")
    archive_fields = {
        "duplicate": ("duplicate_archived_file", Path(processed_folder) / "Duplicates"),
        "not_dot": ("not_dot_archived_file", Path(exceptions_folder) / "Not DOT"),
    }
    if status not in archive_fields:
        raise ReviewValidationError("Only Duplicate and Not DOT records can be restored to Active.")

    archive_field, expected_archive_folder = archive_fields[status]
    archive = Path(result.get(archive_field) or "")
    source = Path(result.get("source_file") or "")
    incoming_folder = Path(incoming_folder).resolve()
    if archive.suffix.lower() != ".pdf" or not archive.is_file():
        raise ReviewValidationError(f"The archived PDF is unavailable: {archive}")
    if archive.resolve().parent != expected_archive_folder.resolve():
        raise ReviewValidationError("The archived PDF is outside its configured archive folder.")
    if source.suffix.lower() != ".pdf" or source.resolve().parent != incoming_folder:
        raise ReviewValidationError("The original source path is not directly inside Incoming.")
    if source.exists():
        raise ReviewValidationError(
            f"Incoming already contains {source.name}; the archived document was not restored."
        )

    expected_hash = result.get("source_sha256")
    expected_size = result.get("source_size")
    if not expected_hash or expected_size is None:
        raise ReviewValidationError(
            "The document has no review fingerprint; it cannot be safely restored."
        )
    digest = hashlib.sha256()
    actual_size = 0
    with archive.open("rb") as archived_file:
        for chunk in iter(lambda: archived_file.read(1024 * 1024), b""):
            digest.update(chunk)
            actual_size += len(chunk)
    if digest.hexdigest() != expected_hash or actual_size != expected_size:
        raise ReviewValidationError(
            "The archived PDF changed after review; it cannot be safely restored."
        )

    audit_details = {
        "source_file": str(source),
        "archived_file": str(archive),
        "previous_status": status,
        "source_sha256": expected_hash,
        "source_size": expected_size,
    }
    try:
        _append_audit(audit_path, {"event": "restore_to_active_started", **audit_details})
    except Exception as error:
        raise ReviewValidationError(f"The restore could not be audited; no file was moved: {error}") from error

    moved = False
    try:
        archive.rename(source)
        moved = True
        _append_audit(audit_path, {"event": "restored_to_active", **audit_details})
    except Exception as error:
        rollback_error = None
        if moved:
            try:
                source.rename(archive)
            except Exception as rollback_exception:
                rollback_error = rollback_exception
        try:
            _append_audit(
                audit_path,
                {
                    "event": "restore_to_active_failed",
                    **audit_details,
                    "reason": str(error),
                    "rollback_error": str(rollback_error) if rollback_error else None,
                },
            )
        except Exception:
            pass
        if rollback_error is not None:
            raise ReviewValidationError(
                "The archived PDF could not be restored or returned to its archive; contact IT. "
                f"Source: {source}. Archive: {archive}. Error: {rollback_error}"
            ) from error
        raise ReviewValidationError(
            f"The archived PDF could not be restored; it remains in its archive: {error}"
        ) from error

    restored = dict(result)
    restored.update(
        {
            "status": "needs_review",
            "reasons": ["RESTORED_TO_ACTIVE_REVIEW"],
            "proposed_filename": None,
            "proposed_destination": None,
            "duplicate_destination": None,
            "duplicate_archived_file": None,
            "duplicate_at_utc": None,
            "not_dot_archived_file": None,
            "not_dot_at_utc": None,
            "restored_at_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    return restored


def approve_document(
    result: dict,
    *,
    audit_path: str | Path,
    unit_folders_root: str | Path,
    incoming_folder: str | Path,
    approved_folder: str | Path,
    farm_asset_folders_root: str | Path | None = None,
    tool_folders_root: str | Path | None = None,
    database_path: str | Path | None = None,
) -> dict:
    source = Path(result.get("source_file") or "")
    destination_value = result.get("proposed_destination")
    destination = Path(destination_value) if destination_value else None
    incoming_folder = Path(incoming_folder).resolve()
    approved_folder = Path(approved_folder).resolve()
    same_folder = os.path.normcase(str(incoming_folder)) == os.path.normcase(str(approved_folder))
    if incoming_folder.exists() and approved_folder.exists():
        same_folder = same_folder or os.path.samefile(incoming_folder, approved_folder)
    if same_folder:
        raise ApprovalError("The configured Incoming and Approved folders must be different.")

    def fail(message: str) -> None:
        try:
            _append_audit(
                audit_path,
                {
                    "event": "approval_failed",
                    "source_file": str(source),
                    "destination": str(destination) if destination else None,
                    "reason": message,
                },
            )
        except Exception as audit_error:
            message = f"{message} Failure logging also failed: {audit_error}"
        raise ApprovalError(message)

    if result.get("status") != "ready_for_review":
        fail("The document is not ready for approval.")
    if not source.is_file():
        fail(f"The source PDF does not exist: {source}")
    if source.suffix.lower() != ".pdf":
        fail("Only PDF source documents can be approved.")
    if source.resolve().parent != incoming_folder:
        fail("The source PDF is not directly inside the configured Incoming folder.")
    expected_hash = result.get("source_sha256")
    expected_size = result.get("source_size")
    if not expected_hash or expected_size is None:
        fail("The document has no review fingerprint; scan it again before approval.")
    digest = hashlib.sha256()
    actual_size = 0
    with source.open("rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
            actual_size += len(chunk)
    if digest.hexdigest() != expected_hash or actual_size != expected_size:
        fail("The source PDF changed since review; scan it again before approval.")
    if destination is None:
        fail("The document has no proposed destination.")

    document_type = (result.get("document_type") or "").upper()
    is_tool = result.get("subject_type") == "tool" or document_type == "CAL"
    unit = normalize_unit(result.get("unit") or "")
    if document_type not in ALLOWED_DOCUMENT_TYPES or (not is_tool and not unit):
        fail("The approved subject or document type is invalid.")
    try:
        controlling_date = _parse_review_date(result.get("controlling_date") or "")
        page_suffix = _normalize_page_suffix(result.get("page_suffix"))
    except ReviewValidationError as error:
        fail(str(error))
    if is_tool:
        if document_type != "CAL" or database_path is None or tool_folders_root is None:
            fail("Calibration approval requires the tool database and Tool Binder root.")
        try:
            tool = get_tool_by_identifier(database_path, result.get("subject_id"))
        except ValueError as error:
            fail(str(error))
        if result.get("tool_database_id") != tool["id"]:
            fail("The selected tool changed or does not match the tool database; scan it again.")
        tool_root = Path(tool_folders_root)
        subject_folder = tool_root / f"Tool_{tool['display_tool_id']}"
        if not subject_folder.is_dir() or subject_folder.resolve().parent != tool_root.resolve():
            fail(f"No safe production Tool Binder was found for {tool['display_tool_id']}.")
        expected_filename = build_tool_filename(tool["display_tool_id"], controlling_date, suffix=page_suffix)
        expected_destination = subject_folder / destination_subfolder(document_type) / expected_filename
        trusted_owner = None
    else:
        trusted_owner = find_asset_owner(database_path, unit) if database_path else result.get("asset_owner")
        if database_path and result.get("asset_owner") != trusted_owner:
            fail("The asset ownership changed or does not match the fleet database; scan it again.")
        try:
            selected_root = asset_folder_root(trusted_owner, unit_folders_root, farm_asset_folders_root)
        except AssetValidationError as error:
            fail(str(error))
        unit_folder = find_unit_folder(selected_root, unit)
        if unit_folder is None:
            fail(f"No production folder was found for unit {unit}.")
        expected_filename = build_filename(unit, document_type, controlling_date, suffix=page_suffix)
        expected_destination = unit_folder / destination_subfolder(document_type) / expected_filename
    if destination.resolve() != expected_destination.resolve():
        fail(
            "The proposed destination does not match the validated production path: "
            f"{expected_destination}"
        )
    if not destination.parent.is_dir():
        fail(f"The destination folder does not exist: {destination.parent}")
    try:
        _append_audit(
            audit_path,
            {
                "event": "approval_started",
                "source_file": str(source),
                "destination": str(destination),
                "source_sha256": expected_hash,
            },
        )
    except Exception as error:
        fail(f"Approval could not start because its audit record could not be written: {error}")

    temporary_path = None
    destination_identity = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=".dotdocs-",
            suffix=".tmp",
            delete=False,
        ) as destination_file, source.open("rb") as source_file:
            temporary_path = Path(destination_file.name)
            shutil.copyfileobj(source_file, destination_file)
            destination_file.flush()
            os.fsync(destination_file.fileno())
        copied_hash = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        if copied_hash != expected_hash or temporary_path.stat().st_size != expected_size:
            raise ApprovalError("The temporary production copy failed content verification.")
        shutil.copystat(source, temporary_path)
        os.link(temporary_path, destination)
        destination_identity = _file_identity(destination)
        temporary_path.unlink()
    except FileExistsError:
        fail(f"The destination file already exists: {destination}")
    except Exception as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        fail(f"The document could not be copied: {error}")

    archive = None
    archive_identity = None
    archive_hash = None
    archive_size = None
    source_removed = False
    certification_id = None
    try:
        approved_folder.mkdir(parents=True, exist_ok=True)
        archive = approved_folder / source.name
        sequence = 2
        while archive.exists():
            archive = approved_folder / f"{source.stem}_{sequence}{source.suffix}"
            sequence += 1
        source.rename(archive)
        source_removed = True
        archive_identity = _file_identity(archive)
        archive_size = archive_identity.st_size
        archive_hash = _file_sha256(archive)
        if archive_size != expected_size or archive_hash != expected_hash:
            raise ApprovalError("The approved source archive failed fingerprint verification.")

        approved = dict(result)
        approved.update(
            {
                "status": "approved",
                "approved_destination": str(destination),
                "approved_archived_file": str(archive) if archive is not None else None,
                "approved_at_utc": datetime.now(timezone.utc).isoformat(),
            }
        )
        if is_tool:
            certification = add_tool_certification(
                database_path,
                tool["id"],
                {
                    "certificate_type": "calibration",
                    "due_date": controlling_date.isoformat(),
                    "result": "unknown",
                    "document_path": str(destination),
                    "document_sha256": expected_hash,
                    "source_review_id": str(source),
                },
            )
            certification_id = certification["id"]
            approved["tool_certification_id"] = certification_id
        _append_audit(
            audit_path,
            {
                "event": "approved",
                "source_file": str(source),
                "archived_file": str(archive) if archive is not None else None,
                "destination": str(destination),
                "unit": approved.get("unit"),
                "subject_type": approved.get("subject_type"),
                "subject_id": approved.get("subject_id"),
                "asset_owner": approved.get("asset_owner"),
                "document_type": approved.get("document_type"),
                "controlling_date": approved.get("controlling_date"),
                "page_suffix": approved.get("page_suffix"),
                "manually_corrected": bool(approved.get("manually_corrected")),
            },
        )
    except Exception as error:
        rollback_errors = []
        if certification_id is not None and database_path is not None:
            try:
                remove_tool_certification(database_path, certification_id)
            except Exception as rollback_error:
                rollback_errors.append(f"calibration history rollback failed: {rollback_error}")
        try:
            if (
                source_removed
                and archive is not None
                and archive_identity is not None
                and archive_hash is not None
                and archive_size is not None
            ):
                recovery_quarantine = archive.with_name(
                    f".{archive.name}.docmarshal-restore-{uuid.uuid4().hex}.tmp"
                )
                archive.rename(recovery_quarantine)
                if not _matches_owned_file(
                    recovery_quarantine, archive_identity, archive_hash, archive_size
                ):
                    if archive.exists():
                        raise ApprovalError(
                            "A changed Approved file was quarantined during rollback, but its archive path is now "
                            f"occupied; contact IT. Quarantine: {recovery_quarantine}"
                        )
                    recovery_quarantine.rename(archive)
                    raise ApprovalError(
                        f"Refusing to restore an Approved file that changed during approval: {archive}"
                    )
                if source.exists():
                    recovery_quarantine.rename(archive)
                    raise ApprovalError(
                        f"Refusing to overwrite a new Incoming file during rollback: {source}"
                    )
                recovery_quarantine.rename(source)
                restored_identity = _file_identity(source)
                if not _matches_owned_file(source, restored_identity, archive_hash, archive_size):
                    raise ApprovalError("The restored Incoming source failed verification.")
        except Exception as rollback_exception:
            rollback_errors.append(f"source recovery: {rollback_exception}")
        try:
            if destination_identity is not None and destination.exists():
                _remove_owned_file_via_quarantine(
                    destination, destination_identity, expected_hash, expected_size
                )
        except Exception as rollback_exception:
            rollback_errors.append(f"production cleanup: {rollback_exception}")
        if rollback_errors:
            fail(
                "Approval failed and rollback could not safely restore every file; contact IT. "
                f"Error: {error}. Rollback errors: {'; '.join(rollback_errors)}"
            )
        fail(
            "Approval failed; the source was restored to Incoming and no production copy was retained: "
            f"{error}"
        )
    return approved
