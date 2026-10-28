"""InvoiceFlow AI — Medicine Table Extractor baseline (Module 02).

Requirements: numpy and an existing PaddleOCR engine. The detector and header
reconstructor are supplied by the calling Colab notebook, to preserve its
previously validated header logic.

This module retains OCR evidence, never corrects an OCR reading from labels,
and never automatically authorizes RPA.
"""

from __future__ import annotations

import copy
import re
import time
import unicodedata
from bisect import bisect_right
from numbers import Integral
from statistics import median
from typing import Callable

import numpy as np

FIELDS = ("stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien")
AMOUNT_FIELDS = ("so_luong", "don_gia", "thanh_tien")


def _fold(value):
    s = unicodedata.normalize("NFD", str(value).lower().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _compact(value):
    return re.sub(r"[^a-z0-9]", "", _fold(value))


def _number(value):
    s = str(value).strip()
    if not re.fullmatch(r"\d[\d.,\s]*", s):
        return None
    return int(re.sub(r"\D", "", s))


def _unit(value):
    raw = str(value).strip()
    aliases = {"vien": "Viên", "hop": "Hộp", "tuyp": "Tuýp",
               "chai": "Chai", "lo": "Lọ", "goi": "Gói",
               "ong": "Ống", "lan": "Lần"}
    return aliases.get(_compact(raw), raw)


def _is_footer(value):
    return _compact(value).startswith(("tongcong", "tongtien", "tongthanhtoan", "congthanhtoan"))


def _split_note(value):
    original = str(value).strip()
    match = re.search(
        r"(?<![a-z0-9])(?:so\s*(?:lo|1o|16)|l[0-9o]\s*sx|han\s*(?:sd|dung)|ngay\s*sx)\b",
        _fold(original),
    )
    if match:
        return original[:match.start()].strip(), original[match.start():].strip()
    return original, None


def _align(reference_ys, field_tokens):
    """One-to-one monotonic alignment, preserving unassigned OCR tokens."""
    n, m = len(reference_ys), len(field_tokens)
    assigned = [[] for _ in range(n)]
    if not n or not m:
        return assigned, list(field_tokens)
    ordered = sorted(field_tokens, key=lambda t: t["cy"])
    if n == m:
        return [[token] for token in ordered], []

    gaps = [reference_ys[i+1] - reference_ys[i] for i in range(n-1)]
    gap = max(18, median(gaps)) if gaps else 55
    penalty = gap * .7
    inf = float("inf")
    dp = [[inf]*(m+1) for _ in range(n+1)]
    back = [[None]*(m+1) for _ in range(n+1)]
    dp[0][0] = 0
    for i in range(n+1):
        for j in range(m+1):
            current = dp[i][j]
            if current == inf:
                continue
            if i < n and current + penalty < dp[i+1][j]:
                dp[i+1][j] = current + penalty
                back[i+1][j] = (i, j, "missing")
            if j < m and current + penalty < dp[i][j+1]:
                dp[i][j+1] = current + penalty
                back[i][j+1] = (i, j, "extra")
            if i < n and j < m:
                distance = abs(reference_ys[i] - ordered[j]["cy"])
                if distance <= max(35, gap*1.4) and current + distance < dp[i+1][j+1]:
                    dp[i+1][j+1] = current + distance
                    back[i+1][j+1] = (i, j, "match")
    unused = []
    i, j = n, m
    while i or j:
        step = back[i][j]
        if step is None:
            raise RuntimeError("Monotone row alignment failed")
        pi, pj, action = step
        if action == "match":
            assigned[pi].append(ordered[pj])
        elif action == "extra":
            unused.append(ordered[pj])
        i, j = pi, pj
    return assigned, unused


def extract_medicine_table(image, ocr_engine, *, detect_header: Callable,
                           reconstruct_headers: Callable):
    """Extract a six-column medicine table from a PIL image.

    `detect_header(text)` and `reconstruct_headers(headers, ocr_raw)` must
    be the previously tested notebook functions. Exactly one OCR call.
    """
    started = time.perf_counter()
    rgb = np.asarray(image.convert("RGB"))
    height, _ = rgb.shape[:2]
    raw = ocr_engine.predict(rgb)[0].json["res"]
    tokens = []
    for idx, (txt, box, conf) in enumerate(zip(raw["rec_texts"], raw["rec_boxes"], raw["rec_scores"])):
        x1, y1, x2, y2 = map(int, box)
        tokens.append({"id": idx, "text": str(txt), "score": float(conf),
                       "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                       "cx": (x1+x2)/2, "cy": (y1+y2)/2})

    def fail(status, code, heads=None):
        return {"status": status, "layout": "UNKNOWN", "document_type": "MEDICINE_INVOICE",
                "headers": heads or [], "items": [], "warnings": [code],
                "ready_for_rpa": False, "ocr_box_count": len(tokens),
                "processing_time": round(time.perf_counter()-started, 3)}

    headers, seen = [], set()
    for token in sorted(tokens, key=lambda t: (t["y1"], t["x1"])):
        if token["cy"] > height*.35:
            continue
        field = "thanh_tien" if _compact(token["text"]) == "sotin" else detect_header(token["text"])
        if field in FIELDS and field not in seen:
            headers.append({"field": field, "ocr_text": token["text"],
                            "x1": token["x1"], "y1": token["y1"], "x2": token["x2"], "y2": token["y2"],
                            "cx": token["cx"], "confidence": token["score"], "source": "detected"})
            seen.add(field)
    if len(headers) >= 2:
        headers = reconstruct_headers(headers, raw)
    head_map = {}
    for h in headers:
        head_map.setdefault(h["field"], h)
    detected = set(head_map)
    if detected != set(FIELDS):
        missing = set(FIELDS)-detected
        return fail("NEEDS_HEADER_REVIEW" if len(detected) >= 4 else "UNSUPPORTED_LAYOUT",
                    "MISSING_HEADERS:"+",".join(sorted(missing)), list(head_map.values()))

    headers = sorted(head_map.values(), key=lambda h: h["cx"])
    hm = {h["field"]: h for h in headers}
    header_bottom = max(h["y2"] for h in headers)
    stt_x = hm["stt"]["cx"]
    stt_gap = abs(hm["ten_dich_vu"]["cx"] - stt_x)
    anchors = []
    for token in tokens:
        if token["cy"] <= header_bottom + 1 or abs(token["cx"] - stt_x) > max(25, stt_gap*.38):
            continue
        match = re.fullmatch(r"\s*(\d{1,3})\s*[.)]?\s*", token["text"])
        if match:
            anchors.append({**token, "stt": int(match.group(1))})
    anchors.sort(key=lambda a: a["cy"])
    if not anchors:
        return fail("NEEDS_REVIEW", "STT_ROW_ANCHORS_NOT_FOUND", headers)

    footers = [t["cy"] for t in tokens if t["cy"] > anchors[-1]["cy"] and _is_footer(t["text"])]
    footer_y = min(footers) if footers else float("inf")
    centers = [h["cx"] for h in headers]
    col_fields = [h["field"] for h in headers]

    def field_of(token):
        return col_fields[min(range(len(headers)), key=lambda i: abs(token["cx"]-centers[i]))]

    numeric = {field: [] for field in AMOUNT_FIELDS}
    units = []
    for token in tokens:
        if token["cy"] <= header_bottom + 1 or token["cy"] >= footer_y:
            continue
        field, txt = field_of(token), token["text"]
        if field in numeric and re.search(r"\d", txt):
            numeric[field].append(token)
        elif field == "don_vi_tinh" and re.search(r"[A-Za-zÀ-ỹ]", txt) and not re.search(r"\d", txt):
            units.append(token)

    stt_ys = [a["cy"] for a in anchors]
    totals, extra_totals = _align(stt_ys, numeric["thanh_tien"])
    baselines = [totals[i][0]["cy"] if totals[i] else anchors[i]["cy"] for i in range(len(anchors))]
    groups, unused = {"thanh_tien": totals}, {"thanh_tien": extra_totals}
    for field in ("so_luong", "don_gia"):
        groups[field], unused[field] = _align(baselines, numeric[field])
    groups["don_vi_tinh"], unused["don_vi_tinh"] = _align(baselines, units)

    boundaries = [(anchors[i]["cy"]+anchors[i+1]["cy"])/2 for i in range(len(anchors)-1)]
    first_gap = anchors[1]["cy"]-anchors[0]["cy"] if len(anchors)>1 else 70
    name_start_y = max(header_bottom+1, anchors[0]["cy"]-first_gap/2)
    name_left = (hm["stt"]["cx"]+hm["ten_dich_vu"]["cx"])/2
    next_x = min(h["cx"] for h in headers if h["cx"] > hm["ten_dich_vu"]["cx"])
    name_right = (hm["ten_dich_vu"]["cx"]+next_x)/2
    nameparts = [[] for _ in anchors]
    notes = [[] for _ in anchors]
    anchor_ids = {a["id"] for a in anchors}
    for token in sorted(tokens, key=lambda t: (t["cy"], t["x1"])):
        if not name_start_y <= token["cy"] < footer_y or token["id"] in anchor_ids or _is_footer(token["text"]):
            continue
        if token["x1"] >= name_right or token["x2"] <= name_left-15:
            continue
        idx = bisect_right(boundaries, token["cy"])
        if idx >= len(anchors):
            continue
        name, note = _split_note(token["text"])
        if note:
            notes[idx].append(note)
        if name and any(c.isalpha() for c in name):
            nameparts[idx].append({"text": name, "confidence": token["score"], "box_id": token["id"]})

    items = []
    for idx, anchor in enumerate(anchors):
        warnings, field_values, raw_by_field = [], {}, {}
        for field in ("so_luong", "don_vi_tinh", "don_gia", "thanh_tien"):
            found = groups[field][idx]
            raw_by_field[field] = [t["text"] for t in found]
            if len(found) == 0:
                value = None
                warnings.append("MISSING_"+field.upper())
            elif len(found) > 1:
                value = None
                warnings.append("AMBIGUOUS_"+field.upper())
            else:
                value = _unit(found[0]["text"]) if field == "don_vi_tinh" else _number(found[0]["text"])
                if value is None:
                    warnings.append("UNPARSEABLE_"+field.upper())
            field_values[field] = value
        name = " ".join(p["text"] for p in nameparts[idx]).strip()
        if not name:
            warnings.append("MISSING_PRODUCT_NAME")
        q, price, total = (field_values[k] for k in ("so_luong", "don_gia", "thanh_tien"))
        calculated = q*price if q is not None and price is not None else None
        difference = total-calculated if calculated is not None and total is not None else None
        if difference is not None and difference != 0:
            warnings.append("AMOUNT_MISMATCH")
        hint = total//q if q and total is not None and total%q==0 and price is None else None
        items.append({"stt": anchor["stt"], "ten_dich_vu": name, **field_values,
                      "notes": notes[idx], "calculated_amount": calculated,
                      "amount_difference": difference, "derived_unit_price_hint": hint,
                      "raw_cells": raw_by_field, "name_source_fragments": nameparts[idx],
                      "name_ocr_confidence": min((p["confidence"] for p in nameparts[idx]), default=None),
                      "name_verification": "UNVERIFIED", "requires_review": bool(warnings),
                      "warnings": warnings})
    review_count = sum(it["requires_review"] for it in items)
    raw_unused = {field: [t["text"] for t in tokens_unused] for field, tokens_unused in unused.items() if tokens_unused}
    warnings = (["ITEMS_REQUIRE_REVIEW"] if review_count else [])
    if raw_unused:
        warnings.append("UNASSIGNED_COLUMN_TOKENS")
    return {"status": "EXTRACTED", "document_type": "MEDICINE_INVOICE", "layout": "MEDICINE_TABLE",
            "headers": headers, "items": items, "detected_rows": len(items), "review_count": review_count,
            "unassigned_cells": raw_unused, "warnings": warnings, "ready_for_rpa": False,
            "ocr_box_count": len(tokens), "processing_time": round(time.perf_counter()-started, 3)}


def _financial_candidate(field, text):
    """Only unresolved numeric-like cells justify financial review.

    Excludes medicine names with digits and expiry notes (e.g. `Han SD: 31/08/2027`).
    Preserves malformed price OCR such as `15 83ó` as actionable.
    """
    s = str(text).strip()
    if field == "so_luong":
        return bool(re.fullmatch(r"\d[\d.,\s]*", s))
    if field in ("don_gia", "thanh_tien"):
        return bool(re.fullmatch(r"\d[\d.,\s]*(?:[^\W\d_])?", s, re.UNICODE))
    if field == "don_vi_tinh":
        return bool(re.fullmatch(r"[^\W\d_]{1,12}", s, re.UNICODE))
    return False


def _valid_int(v, *, positive=False):
    return isinstance(v, Integral) and not isinstance(v, bool) and (v>0 if positive else v>=0)


def finalize_medicine_document(extraction):
    """Quality gate plus classification of OCR tokens not assigned to table cells.

    Unresolved financial-looking cells block approval; medicine names and expiry
    notes are retained for traceability but are not treated as numeric errors.
    """
    output = copy.deepcopy(extraction)
    output["ready_for_rpa"] = False
    if output.get("status") != "EXTRACTED":
        output["qa_status"] = "BLOCKED_EXTRACTION_FAILED"
        output["qa_summary"] = {"total_rows": 0, "formula_ok_rows": 0,
                                "validation_error_rows": 0, "names_pending_confirmation": 0,
                                "has_unassigned_cells": False}
        return output
    items = output.get("items", [])
    if not items:
        output["qa_status"] = "BLOCKED_NO_ITEMS"
        output["qa_summary"] = {"total_rows": 0, "formula_ok_rows": 0,
                                "validation_error_rows": 0, "names_pending_confirmation": 0,
                                "has_unassigned_cells": False}
        return output

    raw_unused = output.get("unassigned_cells", {}) or {}
    actionable, ignored = {}, {}
    for field, values in raw_unused.items():
        for value in values:
            target = actionable if _financial_candidate(field, value) else ignored
            target.setdefault(field, []).append(value)
    output["unassigned_cells_raw"] = raw_unused
    output["unassigned_cells"] = actionable
    output["ignored_unassigned_text"] = ignored
    output["warnings"] = [w for w in output.get("warnings", []) if w != "UNASSIGNED_COLUMN_TOKENS"]
    if actionable:
        output["warnings"].append("UNASSIGNED_NUMERIC_CELLS")

    counts = {}
    for item in items:
        stt = item.get("stt")
        counts[stt] = counts.get(stt, 0)+1
    ok, bad, unverified = 0, 0, 0
    for item in items:
        errors = []
        warnings = list(item.get("warnings", []))
        stt, name = item.get("stt"), str(item.get("ten_dich_vu") or "").strip()
        if not _valid_int(stt, positive=True):
            errors.append("INVALID_STT")
        elif counts[stt]>1:
            errors.append("DUPLICATE_STT")
        if not name:
            errors.append("MISSING_PRODUCT_NAME")
            item["name_verification"] = "MISSING"
        else:
            item["name_verification"] = "UNVERIFIED"
            unverified += 1
            warnings.append("NAME_NEEDS_HUMAN_CONFIRMATION")
        q, unit, price, total = (item.get(k) for k in ("so_luong", "don_vi_tinh", "don_gia", "thanh_tien"))
        if not _valid_int(q, positive=True):
            errors.append("INVALID_OR_MISSING_QUANTITY")
        if not isinstance(unit, str) or not unit.strip():
            errors.append("MISSING_UNIT")
        if not _valid_int(price):
            errors.append("INVALID_OR_MISSING_UNIT_PRICE")
        if not _valid_int(total):
            errors.append("INVALID_OR_MISSING_TOTAL")
        valid_formula = all((_valid_int(q, positive=True), _valid_int(price), _valid_int(total))) and q*price==total
        if all((_valid_int(q, positive=True), _valid_int(price), _valid_int(total))) and not valid_formula:
            errors.append("AMOUNT_MISMATCH")
        item["payment_formula_valid"] = bool(valid_formula)
        item["calculated_amount"] = q*price if _valid_int(q, positive=True) and _valid_int(price) else None
        if valid_formula:
            ok += 1
        suggestions = []
        hint = item.get("derived_unit_price_hint")
        if not _valid_int(price) and _valid_int(hint):
            suggestions.append({"field": "don_gia", "suggested_value": int(hint),
                                "source": "TOTAL_DIVIDED_BY_QUANTITY", "verified": False})
        if not _valid_int(q, positive=True) and _valid_int(price, positive=True) and _valid_int(total) and total%price==0:
            suggestions.append({"field": "so_luong", "suggested_value": total//price,
                                "source": "TOTAL_DIVIDED_BY_UNIT_PRICE", "verified": False})
        item["review_suggestions"] = suggestions
        item["validation_errors"] = errors
        item["warnings"] = list(dict.fromkeys(warnings+errors))
        item["requires_review"] = True
        item["qa_status"] = "VALIDATION_ERROR" if errors else "PENDING_NAME_CONFIRMATION"
        bad += bool(errors)
    output["qa_status"] = ("BLOCKED_VALIDATION_ERRORS" if bad else
                            "BLOCKED_UNASSIGNED_NUMERIC_CELLS" if actionable else "PENDING_HUMAN_REVIEW")
    output["qa_summary"] = {"total_rows": len(items), "formula_ok_rows": ok,
                            "validation_error_rows": bad, "names_pending_confirmation": unverified,
                            "has_unassigned_cells": bool(actionable)}
    output["review_count"] = len(items)
    return output


def process_medicine_final(image, ocr_engine, *, detect_header: Callable, reconstruct_headers: Callable):
    """Single public entry point: OCR -> extraction -> quality gate."""
    return finalize_medicine_document(extract_medicine_table(
        image, ocr_engine, detect_header=detect_header,
        reconstruct_headers=reconstruct_headers))
