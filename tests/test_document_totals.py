"""Document total cross-check and evidence box ids, with mocked OCR only."""
from __future__ import annotations

from PIL import Image
from test_standalone_pipeline import FakeOCR, boxes, payment_data

from invoiceflow_ai.document_totals import parse_amount_text, reconcile_total
from invoiceflow_ai.medical_payment_extractor import process_medical_payment
from invoiceflow_ai.medicine_table_extractor import process_medicine_final
from invoiceflow_ai.pipeline import process_invoice


def box(cx, cy, w=30, h=18):
    return [int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)]


ALIASES = {"stt": "stt", "tên thuc": "ten_dich_vu", "sl": "so_luong", "dvt": "don_vi_tinh",
           "don gia": "don_gia", "so tin": "thanh_tien"}


def detect(text):
    return ALIASES.get(text.lower())


def medicine_lines(footer=()):
    lines = [("STT", box(85, 20, 70, 35)), ("Tên thuc", box(297, 23, 145, 35)),
             ("SL", box(508, 25, 46, 29)), ("DVT", box(597, 25, 78, 30)),
             ("Don gia", box(708, 23, 121, 34)), ("Só tin", box(844, 19, 111, 38)),
             ("1", box(95, 102, 18, 20)), ("Levothyrox 50mcg", box(241, 104, 140, 18)),
             ("68", box(508, 106, 22, 18)), ("Viên", box(579, 104, 50, 18)),
             ("1,520", box(733, 102, 67, 18)), ("103,360", box(867, 100, 95, 18)),
             ("2", box(95, 160, 18, 20)), ("Bisufat", box(241, 162, 100, 18)),
             ("30", box(508, 162, 22, 18)), ("Viên", box(579, 162, 50, 18)),
             ("4,950", box(733, 162, 67, 18)), ("148,500", box(867, 162, 95, 18))]
    return lines + list(footer)


class MockOCR:
    def __init__(self, lines):
        self.lines = lines

    def predict(self, img):
        raw = {"rec_texts": [x[0] for x in self.lines], "rec_boxes": [x[1] for x in self.lines],
               "rec_scores": [.98] * len(self.lines)}
        return [type("R", (), {"json": {"res": raw}})()]


def run_medicine(lines):
    return process_medicine_final(Image.new("RGB", (920, 300), "white"), MockOCR(lines),
                                  detect_header=detect, reconstruct_headers=lambda h, raw: h)


def test_parse_amount_text_accepts_currency_suffix_only():
    assert parse_amount_text("1,234,000") == 1234000
    assert parse_amount_text("1.234.000 đ") == 1234000
    assert parse_amount_text("251860VND") == 251860
    assert parse_amount_text("1,23") is None
    assert parse_amount_text("Tổng") is None


def test_reconcile_statuses():
    total = {"value": 300, "box_id": 9}
    assert reconcile_total("x", [100, 200], total)["status"] == "MATCH"
    mismatch = reconcile_total("x", [100, 150], total)
    assert mismatch["status"] == "MISMATCH" and mismatch["difference"] == 50
    assert reconcile_total("x", [100, None], total)["status"] == "NOT_CHECKABLE"
    assert reconcile_total("x", [100, 200], None)["status"] == "NOT_FOUND"


def test_medicine_total_matches_footer_column():
    result = run_medicine(medicine_lines([("Tổng cộng", box(241, 230, 120, 18)),
                                          ("251,860", box(867, 230, 95, 18))]))
    assert result["document_total"]["value"] == 251860
    assert result["total_check"]["status"] == "MATCH"
    assert result["qa_status"] == "PENDING_HUMAN_REVIEW", result["warnings"]
    assert len(result["items"]) == 2


def test_medicine_total_mismatch_blocks_for_review():
    # Both rows balance on their own; the printed total says a row is missing.
    result = run_medicine(medicine_lines([("Tổng cộng", box(241, 230, 120, 18)),
                                          ("351,860", box(867, 230, 95, 18))]))
    assert all(not it["validation_errors"] for it in result["items"])
    assert result["total_check"]["status"] == "MISMATCH"
    assert result["total_check"]["difference"] == 100000
    assert "TOTAL_MISMATCH" in result["warnings"]
    assert result["qa_status"] == "BLOCKED_TOTAL_MISMATCH"


def test_medicine_total_read_from_footer_label():
    result = run_medicine(medicine_lines([("Tổng cộng: 251.860 đ", box(300, 230, 260, 18))]))
    assert result["total_check"]["status"] == "MATCH"


def test_medicine_without_footer_is_not_blocked():
    result = run_medicine(medicine_lines())
    assert result["total_check"]["status"] == "NOT_FOUND"
    assert "DOCUMENT_TOTAL_NOT_FOUND" in result["warnings"]
    assert result["qa_status"] == "PENDING_HUMAN_REVIEW"


def test_medicine_items_cite_evidence_boxes():
    lines = medicine_lines()
    result = run_medicine(lines)
    evidence = result["items"][1]["evidence_box_ids"]
    texts = {field: [lines[i][0] for i in ids] for field, ids in evidence.items()}
    assert texts == {"stt": ["2"], "ten_dich_vu": ["Bisufat"], "so_luong": ["30"],
                     "don_vi_tinh": ["Viên"], "don_gia": ["4,950"], "thanh_tien": ["148,500"]}


def payment_with_footer(gross_total):
    raw = payment_data()
    extra = boxes([(f"{gross_total:,}", 350, 338), ("109,200", 445, 338),
                   ("0", 520, 338), ("509,300", 610, 338)])
    for key in raw:
        raw[key] = raw[key] + extra[key]
    return raw


def test_payment_column_totals_match():
    result = process_medical_payment(Image.new("RGB", (700, 400)), FakeOCR(payment_with_footer(618_500)))
    assert [c["status"] for c in result["total_checks"]] == ["MATCH"] * 4
    assert result["qa_status"] == "PENDING_HUMAN_REVIEW"
    assert result["qa_summary"]["total_check_status"] == "MATCH"


def test_payment_column_total_mismatch_blocks():
    result = process_medical_payment(Image.new("RGB", (700, 400)), FakeOCR(payment_with_footer(718_500)))
    chi_phi = result["total_checks"][0]
    assert chi_phi["field"] == "chi_phi" and chi_phi["status"] == "MISMATCH"
    assert result["qa_status"] == "BLOCKED_TOTAL_MISMATCH"


def test_pipeline_returns_box_geometry_for_evidence():
    raw = payment_data()
    result = process_invoice(Image.new("RGB", (700, 400)), FakeOCR(raw))
    assert result["image_size"] == [700, 400]
    assert len(result["ocr_boxes"]) == len(raw["rec_texts"])
    first = result["items"][0]["evidence_box_ids"]
    assert [raw["rec_texts"][i] for i in first["chi_phi"]] == ["100,000"]
    assert result["ocr_boxes"][first["chi_phi"][0]] == raw["rec_boxes"][first["chi_phi"][0]]
