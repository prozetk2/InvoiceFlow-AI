"""Shared validation rules for review, approval, CSV export and RPA handoff.

One definition per rule, so a row the backend approves is exactly a row the
handoff accepts. Pure functions; no I/O, no network.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

EXPORT_COLUMNS = ("stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien")

# Operator commands or filler accidentally typed into a product-name cell.
PLACEHOLDER_NAMES = frozenset({
    "review", "reviewed", "approve", "approved", "test", "placeholder",
    "unknown", "n/a", "na", "none", "null", "tbd", "todo",
})

_FORMULA_PREFIX = re.compile(r"^[\s\x00-\x1f']*[=+\-@]")
_PLAIN_INT = re.compile(r"[0-9]+")
_GROUPED_INT = re.compile(r"[0-9]{1,3}(?:[., ][0-9]{3})+")


def is_placeholder_name(value: str) -> bool:
    """True for command/filler words, ignoring case, whitespace and trailing punctuation."""
    return value.strip().rstrip(".!:;").strip().casefold() in PLACEHOLDER_NAMES


def is_formula_like(value: str) -> bool:
    """True if a spreadsheet could evaluate the text, even behind a quote prefix."""
    return bool(_FORMULA_PREFIX.match(value))


def parse_vnd_integer(text: Any) -> int | None:
    """Parse an OCR integer or VND amount without guessing.

    Accepts plain ASCII digits (`1520`) or full thousands groups (`1,520`,
    `1.520.000`, `100 000`), the same rule as the LLM fallback. Rejects
    decimals (`0,5`), short/irregular groups (`1,52`, `12 3`) and non-ASCII
    digits: those return None so the caller flags the cell for review
    instead of storing a wrong value.
    """
    s = str(text).strip()
    if _PLAIN_INT.fullmatch(s) or _GROUPED_INT.fullmatch(s):
        return int(re.sub(r"[^0-9]", "", s))
    return None


def canonical_export_rows(items: Iterable[dict]) -> list[dict]:
    """The six exported fields per row, in export order, names/units stripped."""
    return [{
        "stt": int(item["stt"]),
        "ten_dich_vu": str(item["ten_dich_vu"]).strip(),
        "so_luong": int(item["so_luong"]),
        "don_vi_tinh": str(item["don_vi_tinh"]).strip(),
        "don_gia": int(item["don_gia"]),
        "thanh_tien": int(item["thanh_tien"]),
    } for item in items]


def payload_sha256(rows: list[dict]) -> str:
    """SHA-256 of canonical rows; backend (at approval) and handoff must agree."""
    serialized = json.dumps(canonical_export_rows(rows), ensure_ascii=False,
                            sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
