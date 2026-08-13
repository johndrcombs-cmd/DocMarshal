from datetime import date

import pytest

from dotdocs.tool_calibration import calibration_status


@pytest.mark.parametrize(
    ("due_date", "today", "result", "days", "message"),
    (
        (date(2026, 10, 1), date(2026, 8, 13), "current", 49, "Current — expires in 49 days"),
        (date(2026, 9, 12), date(2026, 8, 13), "due_soon", 30, "Due Soon — expires in 30 days"),
        (date(2026, 8, 13), date(2026, 8, 13), "due_soon", 0, "Due Soon — expires today"),
        (date(2026, 8, 12), date(2026, 8, 13), "expired", -1, "Expired — expired 1 day ago"),
        (date(2026, 7, 14), date(2026, 8, 13), "expired", -30, "Expired — expired 30 days ago"),
        (None, date(2026, 8, 13), "no_date", None, "No expiration date recorded"),
    ),
)
def test_calibration_status_boundaries(due_date, today, result, days, message):
    status = calibration_status(due_date, today=today)

    assert status.code == result
    assert status.days_remaining == days
    assert status.message == message


def test_failed_calibration_is_immediately_unusable_even_with_future_due_date():
    status = calibration_status(date(2027, 1, 1), today=date(2026, 8, 13), result="fail")

    assert status.code == "failed"
    assert status.label == "Failed"
    assert status.message == "Failed calibration — tool is not approved for use"


def test_status_accepts_iso_date_and_rejects_invalid_warning_window():
    assert calibration_status("2026-09-01", today=date(2026, 8, 13)).code == "due_soon"
    with pytest.raises(ValueError, match="warning window"):
        calibration_status("2026-09-01", today=date(2026, 8, 13), warning_days=-1)
