"""Document-level total cross-check: sum of row amounts vs the printed footer total.

A row can pass its own `quantity × unit price = amount` check and still be
missing entirely; only the printed document total reveals that. The footer
value is OCR evidence too, so a mismatch sends the document to review, it
never rewrites a row. Pure functions; no I/O.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from numbers import Integral

from .validation import parse_vnd_integer

_CURRENCY_SUFFIX = re.compile(r"\s*(?:vnđ|vnd|đồng|dong|đ)\s*$", re.IGNORECASE)
_TRAILING_AMOUNT = re.compile(r"([0-9][0-9., ]*[0-9])\s*(?:vnđ|vnd|đồng|dong|đ)?\s*$", re.IGNORECASE)


def parse_amount_text(text: str) -> int | None:
    """`1,234,000`, `1.234.000 đ` or `1234000VND` -> int; anything else -> None."""
    return parse_vnd_integer(_CURRENCY_SUFFIX.sub("", str(text).strip()))


def footer_amount(tokens: list[dict], footer: dict, column_cx: float, *,
                  row_tolerance: float = 20, column_tolerance: float = 90,
                  allow_label_amount: bool = True) -> dict | None:
    """The printed total on the footer line, read from the amount column.

    `tokens` need `id`, `text`, `cx`, `cy`. A value sitting in the amount
    column on the footer's line wins; otherwise a number at the end of the
    footer label itself (`Tổng cộng: 1,234,000`), unless `allow_label_amount`
    is False (multi-column footers, where the label value has no column).
    Ambiguous -> None.
    """
    candidates = []
    for token in tokens:
        if token["id"] == footer["id"] or abs(token["cy"] - footer["cy"]) > row_tolerance:
            continue
        if abs(token["cx"] - column_cx) > column_tolerance:
            continue
        value = parse_amount_text(token["text"])
        if value is not None:
            candidates.append((abs(token["cx"] - column_cx), token, value))
    if candidates:
        candidates.sort(key=lambda c: c[0])
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            return None
        _, token, value = candidates[0]
        return {"value": value, "raw_text": token["text"], "box_id": token["id"]}
    match = _TRAILING_AMOUNT.search(str(footer["text"])) if allow_label_amount else None
    if match:
        value = parse_vnd_integer(match.group(1).strip())
        if value is not None:
            return {"value": value, "raw_text": footer["text"], "box_id": footer["id"]}
    return None


def _is_amount(value) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool) and value >= 0


def reconcile_total(field: str, row_values: Iterable, document_total: dict | None) -> dict:
    """Compare the sum of one row field with the printed total.

    status: MATCH, MISMATCH, NOT_FOUND (no printed total) or NOT_CHECKABLE
    (some row value missing, so the sum would be meaningless).
    """
    values = list(row_values)
    printed = document_total["value"] if document_total else None
    check = {"field": field, "document_total": printed,
             "document_total_box_id": document_total["box_id"] if document_total else None,
             "rows_sum": None, "difference": None}
    if printed is None:
        return {**check, "status": "NOT_FOUND"}
    if not values or not all(_is_amount(v) for v in values):
        return {**check, "status": "NOT_CHECKABLE"}
    rows_sum = sum(values)
    difference = printed - rows_sum
    return {**check, "rows_sum": rows_sum, "difference": difference,
            "status": "MATCH" if difference == 0 else "MISMATCH"}
