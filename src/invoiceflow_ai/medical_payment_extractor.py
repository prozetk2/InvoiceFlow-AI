"""Module 01: six-field-compatible medical BHYT payment extraction baseline.

Column semantics are deliberately different: chi_phi is the full service cost,
not the patient out-of-pocket amount. No OCR values are filled from GT.
"""
from __future__ import annotations

import copy
import re
import time
from bisect import bisect_right
from statistics import median
from typing import Any

import numpy as np

from .header_detection import compact

PAYMENT_FIELDS = ("chi_phi", "bhyt_chi", "so_giam", "benh_nhan_thanh_toan")
SERVICE = re.compile(r"^\s*(\d{1,2})\s*[.)]?\s+(.+)$")
QTY = re.compile(r"\(\s*S[\s.\-_]*L[^:)]{0,20}:\s*(\d+)\s*([^)]+)\)", re.I)


def _money(text: str) -> int | None:
    value = str(text).strip()
    return int(re.sub(r"\D", "", value)) if re.fullmatch(r"\d[\d.,\s]*", value) else None


def _is_footer(text: str) -> bool:
    return compact(text).startswith(("tongcong", "tongtien", "tongthanhtoan", "grandtotal"))


def _is_name_note(text: str) -> bool:
    return bool(QTY.search(text))


def _unit(text: str) -> str | None:
    value = text.strip()
    if compact(value) in ("lan", "laan"):
        return "Lần"
    return value or None


def extract_medical_payment(image: Any, ocr_engine: Any) -> dict:
    """Return structured services and separate settlement fields; one OCR call."""
    started = time.perf_counter()
    rgb = np.asarray(image.convert("RGB"))
    height = rgb.shape[0]
    raw = ocr_engine.predict(rgb)[0].json["res"]
    tokens = []
    for i, (text, box, score) in enumerate(zip(raw["rec_texts"], raw["rec_boxes"], raw["rec_scores"])):
        x1, y1, x2, y2 = map(int, box)
        tokens.append({"id": i, "text": str(text), "score": float(score), "x1": x1, "y1": y1,
                       "x2": x2, "y2": y2, "cx": (x1 + x2) / 2, "cy": (y1 + y2) / 2})

    def fail(message: str) -> dict:
        return {"status": "NEEDS_REVIEW", "document_type": "MEDICAL_PAYMENT", "items": [],
                "warnings": [message], "ready_for_rpa": False,
                "processing_time": round(time.perf_counter() - started, 3)}

    top = [t for t in tokens if t["cy"] < height * 0.25]
    def header(key: str):
        return next((t for t in top if compact(t["text"]) == key), None)
    cost, insurance, patient = header("chiphi"), header("bhyt"), header("bn")
    if any(v is None for v in (cost, insurance, patient)):
        return fail("PAYMENT_HEADERS_NOT_FOUND")
    discounts = [t for t in top if compact(t["text"]) in ("so", "sogiam", "giam")
                 and insurance["cx"] < t["cx"] < patient["cx"]]
    if not discounts:
        return fail("DISCOUNT_HEADER_NOT_FOUND")
    discount = min(discounts, key=lambda t: abs(t["cx"] - (insurance["cx"] + patient["cx"]) / 2))
    centers = {"chi_phi": cost["cx"], "bhyt_chi": insurance["cx"],
               "so_giam": discount["cx"], "benh_nhan_thanh_toan": patient["cx"]}
    header_bottom = max(h["y2"] for h in (cost, insurance, patient, discount))

    anchors = []
    for token in tokens:
        if token["cy"] <= height * 0.23 or token["cx"] >= cost["cx"] - 40:
            continue
        match = SERVICE.match(token["text"])
        if match:
            anchors.append({**token, "stt": int(match.group(1)), "start_name": match.group(2).strip()})
    anchors.sort(key=lambda t: t["cy"])
    if not anchors:
        return fail("SERVICE_ROWS_NOT_FOUND")

    boundaries = [(a["cy"] + b["cy"]) / 2 for a, b in zip(anchors, anchors[1:])]
    gap = (anchors[1]["cy"] - anchors[0]["cy"]) if len(anchors) > 1 else 70
    start_y = max(header_bottom + 1, anchors[0]["cy"] - gap / 2)
    footer_values = [t["cy"] for t in tokens if t["cy"] > anchors[-1]["cy"] and _is_footer(t["text"])]
    footer_y = min(footer_values) if footer_values else float("inf")

    # Financial amounts belong near numbered service anchors, not quantity notes.
    amounts = [{k: [] for k in PAYMENT_FIELDS} for _ in anchors]
    for token in tokens:
        number = _money(token["text"])
        if number is None or not (cost["cx"] - 40 <= token["cx"] <= patient["cx"] + 60):
            continue
        i = min(range(len(anchors)), key=lambda j: abs(token["cy"] - anchors[j]["cy"]))
        if abs(token["cy"] - anchors[i]["cy"]) > 25:
            continue
        nearest = min(PAYMENT_FIELDS, key=lambda field: abs(token["cx"] - centers[field]))
        if abs(token["cx"] - centers[nearest]) <= 55:
            amounts[i][nearest].append(number)

    fragments = [[] for _ in anchors]
    notes = [[] for _ in anchors]
    anchor_map = {a["id"]: a for a in anchors}
    for token in sorted(tokens, key=lambda t: (t["cy"], t["x1"])):
        if not start_y <= token["cy"] < footer_y or token["cx"] >= cost["cx"] - 35:
            continue
        if _is_footer(token["text"]):
            continue
        i = bisect_right(boundaries, token["cy"])
        text = anchor_map[token["id"]]["start_name"] if token["id"] in anchor_map else token["text"]
        notes[i].extend(m.group(0) for m in QTY.finditer(text))
        text = QTY.sub(" ", text).strip()
        if text and any(c.isalpha() for c in text):
            fragments[i].append({"text": text, "confidence": token["score"], "box_id": token["id"]})

    items = []
    for i, anchor in enumerate(anchors):
        warnings = []
        found_qty = [QTY.search(note) for note in notes[i]]
        quantities = [int(m.group(1)) for m in found_qty if m]
        units = [_unit(m.group(2)) for m in found_qty if m]
        quantity = quantities[0] if len(quantities) == 1 else None
        unit = units[0] if len(units) == 1 else None
        if quantity is None:
            warnings.append("MISSING_OR_AMBIGUOUS_QUANTITY")
        if not unit:
            warnings.append("MISSING_OR_AMBIGUOUS_UNIT")
        details = {}
        for field in PAYMENT_FIELDS:
            cells = amounts[i][field]
            details[field] = cells[0] if len(cells) == 1 else None
            if len(cells) != 1:
                warnings.append("MISSING_OR_AMBIGUOUS_" + field.upper())
        calculated = None
        if all(details[k] is not None for k in ("chi_phi", "bhyt_chi", "so_giam")):
            calculated = details["chi_phi"] - details["bhyt_chi"] - details["so_giam"]
        if calculated is not None and details["benh_nhan_thanh_toan"] is not None and calculated != details["benh_nhan_thanh_toan"]:
            warnings.append("PAYMENT_MISMATCH")
        name = " ".join(p["text"] for p in fragments[i]).strip()
        if not name:
            warnings.append("MISSING_SERVICE_NAME")
        gross = details["chi_phi"]
        if quantity != 1:
            warnings.append("UNIT_PRICE_NOT_DIRECTLY_AVAILABLE")
        items.append({
            "stt": anchor["stt"], "ten_dich_vu": name,
            "so_luong": quantity, "don_vi_tinh": unit,
            "don_gia": gross if quantity == 1 else None, "thanh_tien": gross,
            "payment_details": {**details, "calculated_patient_payment": calculated},
            "quantity_notes": notes[i], "name_source_fragments": fragments[i],
            "name_verification": "UNVERIFIED", "requires_review": bool(warnings),
            "warnings": warnings,
        })
    return {"status": "EXTRACTED_PAYMENT", "document_type": "MEDICAL_PAYMENT", "items": items,
            "detected_rows": len(items), "review_count": sum(bool(i["warnings"]) for i in items),
            "ready_for_rpa": False, "column_centers": centers,
            "processing_time": round(time.perf_counter() - started, 3)}


def finalize_medical_payment(extraction: dict) -> dict:
    """Conservative QA: all OCR service names require manual confirmation."""
    out = copy.deepcopy(extraction)
    out["ready_for_rpa"] = False
    items = out.get("items", [])
    if out.get("status") != "EXTRACTED_PAYMENT" or not items:
        out["qa_status"] = "BLOCKED_EXTRACTION_FAILED"
        return out
    errors = 0
    for item in items:
        problem = list(item.get("warnings", []))
        q, price, total = (item.get(f) for f in ("so_luong", "don_gia", "thanh_tien"))
        if not (isinstance(q, int) and q > 0 and isinstance(price, int) and price >= 0 and isinstance(total, int) and total >= 0 and q * price == total):
            problem.append("SERVICE_TOTAL_INVALID_OR_MISSING")
        d = item.get("payment_details") or {}
        if not all(isinstance(d.get(k), int) for k in PAYMENT_FIELDS) or d["chi_phi"] - d["bhyt_chi"] - d["so_giam"] != d["benh_nhan_thanh_toan"]:
            problem.append("PAYMENT_FORMULA_INVALID")
        if not item.get("ten_dich_vu"):
            problem.append("MISSING_SERVICE_NAME")
        item["warnings"] = list(dict.fromkeys(problem + ["NAME_NEEDS_HUMAN_CONFIRMATION"]))
        item["name_verification"] = "UNVERIFIED"
        item["requires_review"] = True
        item["validation_errors"] = [p for p in problem if p != "NAME_NEEDS_HUMAN_CONFIRMATION"]
        errors += bool(item["validation_errors"])
    out["qa_status"] = "BLOCKED_VALIDATION_ERRORS" if errors else "PENDING_HUMAN_REVIEW"
    out["review_count"] = len(items)
    out["qa_summary"] = {"total_rows": len(items), "validation_error_rows": errors,
                         "names_pending_confirmation": len(items)}
    return out


def process_medical_payment(image: Any, ocr_engine: Any) -> dict:
    return finalize_medical_payment(extract_medical_payment(image, ocr_engine))
