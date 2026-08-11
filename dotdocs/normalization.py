import re
from typing import Any


def _alphanumeric(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def normalize_unit(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"\d+", text)
    return str(int(match.group(0))) if match else ""


def normalize_plate(value: Any) -> str:
    return _alphanumeric(value)


def normalize_vin(value: Any) -> str:
    return _alphanumeric(value)
