"""Evidence for human review: which OCR boxes each extracted value came from.

Extractors cite OCR box ids per field (`evidence_box_ids`); the pipeline
stores box geometry (`ocr_boxes`, coordinates only). This module joins the
two so a reviewer sees the source pixels next to each value, and draws them
on the stored image. Pure functions apart from Pillow; no I/O.
"""
from __future__ import annotations

import io
from typing import Any

from .validation import EXPORT_COLUMNS

PAYMENT_FIELDS = ("chi_phi", "bhyt_chi", "so_giam", "benh_nhan_thanh_toan")
EVIDENCE_FIELDS = (*EXPORT_COLUMNS, *PAYMENT_FIELDS)
# One colour per field, readable on white paper.
FIELD_COLORS = {
    "stt": (99, 99, 99), "ten_dich_vu": (31, 119, 180), "so_luong": (44, 160, 44),
    "don_vi_tinh": (148, 103, 189), "don_gia": (255, 127, 14), "thanh_tien": (214, 39, 40),
    "chi_phi": (214, 39, 40), "bhyt_chi": (23, 190, 207), "so_giam": (188, 189, 34),
    "benh_nhan_thanh_toan": (227, 119, 194), "document_total": (0, 0, 0),
}


def _box(geometry: list, box_id: Any) -> list[int] | None:
    if isinstance(box_id, bool) or not isinstance(box_id, int) or not 0 <= box_id < len(geometry):
        return None
    box = geometry[box_id]
    return list(box) if isinstance(box, list) and len(box) == 4 else None


def build_evidence(result: dict) -> dict:
    """Per row and field, the OCR boxes behind the extracted value.

    Rows without cited ids (e.g. imported results from older versions) get
    empty field lists; the reviewer then checks the whole image as before.
    """
    geometry = result.get("ocr_boxes") or []
    rows = []
    for index, item in enumerate(result.get("items") or []):
        if not isinstance(item, dict):
            continue
        cited = item.get("evidence_box_ids") or {}
        fields = {}
        for field in EVIDENCE_FIELDS:
            ids = cited.get(field)
            if not isinstance(ids, list):
                continue
            boxes = [b for b in (_box(geometry, i) for i in ids) if b]
            if boxes:
                fields[field] = boxes
        rows.append({"index": index, "stt": item.get("stt"), "fields": fields})
    totals = []
    single = result.get("document_total")
    printed = {"thanh_tien": single} if single else (result.get("document_totals") or {})
    for field, total in printed.items():
        if isinstance(total, dict):
            box = _box(geometry, total.get("box_id"))
            if box:
                totals.append({"field": field, "box": box})
    checks = result.get("total_checks") or ([result["total_check"]] if result.get("total_check") else [])
    return {"image_size": result.get("image_size"), "rows": rows, "document_totals": totals,
            "total_checks": checks, "has_geometry": bool(geometry)}


def render_overlay(image_bytes: bytes, evidence: dict, *, row_index: int | None = None) -> bytes:
    """PNG of the source image with evidence boxes drawn.

    `row_index` None draws every row thinly; a row index draws that row
    boldly and labels each box with its field name.
    """
    from PIL import Image, ImageDraw

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    draw = ImageDraw.Draw(image)
    for row in evidence.get("rows", []):
        if row_index is not None and row["index"] != row_index:
            continue
        for field, boxes in row["fields"].items():
            color = FIELD_COLORS.get(field, (0, 0, 0))
            for x1, y1, x2, y2 in boxes:
                draw.rectangle([x1 - 2, y1 - 2, x2 + 2, y2 + 2], outline=color,
                               width=3 if row_index is not None else 1)
                if row_index is not None:
                    draw.text((x1, max(0, y1 - 13)), field, fill=color)
    for total in evidence.get("document_totals", []):
        x1, y1, x2, y2 = total["box"]
        draw.rectangle([x1 - 2, y1 - 2, x2 + 2, y2 + 2], outline=FIELD_COLORS["document_total"], width=2)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
