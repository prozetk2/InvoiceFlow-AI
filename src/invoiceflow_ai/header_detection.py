"""Deterministic Vietnamese invoice header detection and multiline repair.

OCR heuristics, not a trained table layout model. Never uses GT labels.
"""
from __future__ import annotations

import re
import unicodedata
from statistics import median

ALIASES = {
    "stt": "stt", "sott": "stt", "sothutu": "stt",
    "tenthuoc": "ten_dich_vu", "tenthuc": "ten_dich_vu", "tenthu": "ten_dich_vu",
    "tendichvu": "ten_dich_vu", "tenhang": "ten_dich_vu", "tenvattu": "ten_dich_vu",
    "sl": "so_luong", "soluong": "so_luong", "luong": "so_luong", "sll": "so_luong",
    "dvt": "don_vi_tinh", "donvitinh": "don_vi_tinh",
    "dongia": "don_gia", "dongla": "don_gia", "donglaa": "don_gia",
    "thanhtien": "thanh_tien", "thanhtin": "thanh_tien", "thnhtin": "thanh_tien",
    "sotien": "thanh_tien", "sotin": "thanh_tien",
}


def compact(text: str) -> str:
    norm = unicodedata.normalize("NFD", str(text).lower().replace("đ", "d"))
    ascii_like = "".join(c for c in norm if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]", "", ascii_like)


def detect_header(text: str) -> str | None:
    return ALIASES.get(compact(text))


def reconstruct_headers(headers: list[dict], raw: dict) -> list[dict]:
    """Recover missing *header* fields from nearby OCR tokens, preserving geometry.

    Restrict candidates to the header row band estimated from already detected
    headers. Never invent header coordinates or count a body cell as a header.
    """
    if not headers:
        return []
    result = [dict(h) for h in headers]
    present = {h["field"] for h in result}
    heights = [max(8, h["y2"] - h["y1"]) for h in result]
    ref_height = median(heights)
    low_y = min(h["y1"] for h in result) - 0.4 * ref_height
    high_y = max(h["y2"] for h in result) + 0.7 * ref_height
    texts = raw.get("rec_texts", [])
    boxes = raw.get("rec_boxes", [])
    scores = raw.get("rec_scores", [])
    candidates = []
    for text, box, conf in zip(texts, boxes, scores):
        if len(box) != 4:
            continue
        x1, y1, x2, y2 = map(int, box)
        cy = (y1 + y2) / 2
        if not low_y <= cy <= high_y:
            continue
        candidates.append({"text": str(text), "box": [x1, y1, x2, y2], "confidence": float(conf)})
    candidates.sort(key=lambda t: (t["box"][0], t["box"][1]))
    # Prefer combined OCR headers: 'Tên' + 'thuc', 'só' + 'lrong'.
    for a in candidates:
        for b in candidates:
            if a is b:
                continue
            ax1, ay1, ax2, ay2 = a["box"]
            bx1, by1, bx2, by2 = b["box"]
            if abs((ax1 + ax2) / 2 - (bx1 + bx2) / 2) > max(35, (ax2 - ax1), (bx2 - bx1)):
                continue
            # Vertical split, two OCR strings in top-to-bottom order.
            if ay1 >= by1 or by1 - ay2 > ref_height * 1.3:
                continue
            merged = compact(a["text"] + b["text"])
            field = ALIASES.get(merged)
            if field and field not in present:
                result.append({
                    "field": field, "ocr_text": a["text"] + " " + b["text"],
                    "x1": min(ax1, bx1), "y1": min(ay1, by1),
                    "x2": max(ax2, bx2), "y2": max(ay2, by2),
                    "cx": (min(ax1, bx1) + max(ax2, bx2)) / 2,
                    "confidence": min(a["confidence"], b["confidence"]),
                    "source": "merged",
                })
                present.add(field)
    return result
