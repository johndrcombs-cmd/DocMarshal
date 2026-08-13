from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CalibrationStatus:
    code: str
    label: str
    days_remaining: int | None
    message: str


def _due_date(value: date | str | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as error:
        raise ValueError("Calibration due date must use YYYY-MM-DD.") from error


def calibration_status(
    due_date: date | str | None,
    *,
    today: date | None = None,
    warning_days: int = 30,
    result: str | None = None,
) -> CalibrationStatus:
    """Return a text-first calibration status suitable for tables and binder spines."""
    if warning_days < 0:
        raise ValueError("Calibration warning window cannot be negative.")
    if str(result or "").strip().lower() == "fail":
        return CalibrationStatus(
            "failed",
            "Failed",
            None,
            "Failed calibration — tool is not approved for use",
        )
    due = _due_date(due_date)
    if due is None:
        return CalibrationStatus("no_date", "No Date", None, "No expiration date recorded")
    current_date = today or date.today()
    days = (due - current_date).days
    if days < 0:
        elapsed = abs(days)
        noun = "day" if elapsed == 1 else "days"
        return CalibrationStatus("expired", "Expired", days, f"Expired — expired {elapsed} {noun} ago")
    if days <= warning_days:
        remaining = "today" if days == 0 else f"in {days} {'day' if days == 1 else 'days'}"
        return CalibrationStatus("due_soon", "Due Soon", days, f"Due Soon — expires {remaining}")
    return CalibrationStatus("current", "Current", days, f"Current — expires in {days} days")
