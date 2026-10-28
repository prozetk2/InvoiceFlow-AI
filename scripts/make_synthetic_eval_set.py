"""Generate the synthetic evaluation set under eval/synthetic/.

Writes, per document, a ground-truth label file (gt/) and a replayable OCR
fixture (ocr/) laid out like a PaddleOCR result. Each document injects one
named OCR failure (typo, missing cell, dropped row, broken header, ...) so the
harness has something real to measure. Fully synthetic: no patient data, and
the resulting numbers say nothing about accuracy on real scans.

    python scripts/make_synthetic_eval_set.py
    invoiceflow eval-predict eval/synthetic/ocr --output eval/synthetic/pred
    invoiceflow eval eval/synthetic/gt eval/synthetic/pred --split test
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "eval" / "synthetic"

MED_COLUMNS = {"stt": 60, "ten_dich_vu": 260, "so_luong": 470, "don_vi_tinh": 550,
               "don_gia": 660, "thanh_tien": 800}
MED_HEADERS = {"stt": "STT", "ten_dich_vu": "Tên thuốc", "so_luong": "SL", "don_vi_tinh": "ĐVT",
               "don_gia": "Đơn giá", "thanh_tien": "Thành tiền"}


def token(text, cx, cy, width=None, height=20, score=0.97):
    width = width or max(24, 9 * len(str(text)))
    return (str(text), [int(cx - width / 2), int(cy - height / 2), int(cx + width / 2), int(cy + height / 2)],
            score)


def med_rows(spec):
    return [{"stt": i + 1, "ten_dich_vu": name, "so_luong": q, "don_vi_tinh": unit,
             "don_gia": price, "thanh_tien": q * price} for i, (name, q, unit, price) in enumerate(spec)]


def medicine_ocr(rows, *, header_override=None, drop_rows=(), drop_cells=(), text_override=None):
    headers = {**MED_HEADERS, **(header_override or {})}
    tokens = [token(headers[f], cx, 40) for f, cx in MED_COLUMNS.items()]
    y = 110
    for row in rows:
        if row["stt"] not in drop_rows:
            for field, cx in MED_COLUMNS.items():
                if (row["stt"], field) in drop_cells:
                    continue
                value = (text_override or {}).get((row["stt"], field), row[field])
                if field in ("don_gia", "thanh_tien"):
                    value = value if isinstance(value, str) else f"{value:,}"
                if field == "ten_dich_vu":
                    # Names are left-aligned in their column.
                    width = 9 * len(str(value))
                    tokens.append(token(value, 170 + width / 2, y, width=width))
                else:
                    tokens.append(token(value, cx, y))
        y += 50
    total = sum(r["thanh_tien"] for r in rows)
    tokens += [token("Tổng cộng", 200, y + 10), token(f"{total:,}", MED_COLUMNS["thanh_tien"], y + 10)]
    return tokens, (900, y + 80)


PAY_COLUMNS = {"chi_phi": 350, "bhyt_chi": 445, "so_giam": 520, "benh_nhan_thanh_toan": 610}


def payment_rows(spec):
    rows = []
    for i, (name, cost, insurance) in enumerate(spec):
        rows.append({"stt": i + 1, "ten_dich_vu": name, "so_luong": 1, "don_vi_tinh": "Lần",
                     "don_gia": cost, "thanh_tien": cost,
                     "payment_details": {"chi_phi": cost, "bhyt_chi": insurance, "so_giam": 0,
                                         "benh_nhan_thanh_toan": cost - insurance}})
    return rows


def payment_ocr(rows, *, text_override=None):
    tokens = [token("Nội dung", 150, 46), token("Chi phí", 350, 45), token("BHYT", 445, 35),
              token("Số giảm", 520, 35, width=50), token("BN", 610, 19)]
    y = 120
    for row in rows:
        name = f"{row['stt']} {row['ten_dich_vu']}"
        tokens += [token(name, 130, y, width=190), token("(S.Luong: 1 Lần)", 130, y + 18, width=150)]
        for field, cx in PAY_COLUMNS.items():
            value = (text_override or {}).get((row["stt"], field), f"{row['payment_details'][field]:,}")
            tokens.append(token(value, cx, y + 3, width=60))
        y += 65
    tokens.append(token("Tổng cộng:", 90, y + 10))
    for field, cx in PAY_COLUMNS.items():
        tokens.append(token(f"{sum(r['payment_details'][field] for r in rows):,}", cx, y + 10, width=60))
    return tokens, (700, y + 80)


def build(root: Path = ROOT) -> None:
    med_a = med_rows([("Amoxicillin 500mg", 20, "Viên", 1200), ("Paracetamol 500mg", 10, "Viên", 800),
                      ("Siro ho thảo dược 100ml", 1, "Chai", 45000)])
    med_b = med_rows([("MEBEVER MR CAPSULES 200mg", 30, "Viên", 4950), ("Vitamin C 500mg", 20, "Viên", 600),
                      ("Kem bôi da 15g", 1, "Tuýp", 38000)])
    med_c = med_rows([("Omeprazol 20mg", 14, "Viên", 2100), ("Loratadin 10mg", 10, "Viên", 1500)])
    med_d = med_rows([("Cefuroxim 250mg", 10, "Viên", 6500), ("Men vi sinh", 10, "Gói", 4200),
                      ("Nước muối sinh lý 10ml", 5, "Lọ", 3000), ("Vitamin B1 100mg", 30, "Viên", 500)])
    med_e = med_rows([("Ibuprofen 400mg", 10, "Viên", 1800), ("Gạc vô trùng", 2, "Hộp", 12000)])
    pay_a = payment_rows([("Khám bệnh", 100000, 50600), ("Xét nghiệm máu", 368500, 0),
                          ("Siêu âm ổ bụng", 150000, 58600)])
    pay_b = payment_rows([("Khám chuyên khoa", 120000, 60000), ("Chụp X-quang", 180000, 90000)])

    docs = {
        # id: (family, split, gt rows, ocr tokens + size, what this case exercises)
        "med_01_clean": ("MEDICINE_INVOICE", "dev", med_a, medicine_ocr(med_a), "clean baseline"),
        "med_02_name_typos": ("MEDICINE_INVOICE", "test", med_b, medicine_ocr(med_b, text_override={
            (1, "ten_dich_vu"): "MEBEVER MR CAPSUL.ES 200mg", (3, "ten_dich_vu"): "Kem boi da 15g"}),
            "OCR name typo and lost accents"),
        "med_03_missing_price": ("MEDICINE_INVOICE", "test", med_c,
                                 medicine_ocr(med_c, drop_cells={(2, "don_gia")}), "one unit price not read"),
        "med_04_dropped_row": ("MEDICINE_INVOICE", "test", med_d, medicine_ocr(med_d, drop_rows={3}),
                               "a whole row lost; only the footer total reveals it"),
        "med_05_broken_header": ("MEDICINE_INVOICE", "test", med_e,
                                 medicine_ocr(med_e, header_override={"thanh_tien": "Th@nh t!en"}),
                                 "amount header unreadable: extractor must stop, not guess"),
        "pay_06_clean": ("MEDICAL_PAYMENT", "test", pay_a, payment_ocr(pay_a), "clean payment receipt"),
        "pay_07_bad_amount": ("MEDICAL_PAYMENT", "test", pay_b, payment_ocr(pay_b, text_override={
            (2, "benh_nhan_thanh_toan"): "90,00"}), "patient amount misread as an irregular number"),
        "other_08_lab_report": ("OTHER", "test", [], ([token("KẾT QUẢ XÉT NGHIỆM", 350, 40, width=260),
                                                      token("Glucose", 120, 120), token("5.4 mmol/L", 400, 120)],
                                                     (700, 400)), "unsupported document: must abstain"),
    }
    for sub in ("gt", "ocr"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    for doc_id, (family, split, rows, (tokens, size), note) in docs.items():
        gt = {"document_family": family, "split": split, "synthetic_case": note, "items": rows}
        ocr = {"image_size": list(size), "rec_texts": [t[0] for t in tokens],
               "rec_boxes": [t[1] for t in tokens], "rec_scores": [t[2] for t in tokens]}
        (root / "gt" / f"{doc_id}.json").write_text(json.dumps(gt, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
        (root / "ocr" / f"{doc_id}.json").write_text(json.dumps(ocr, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(docs)} synthetic documents to {root}")


if __name__ == "__main__":
    build()
