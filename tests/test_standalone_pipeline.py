"""New package-level integration tests, using synthetic and mocked OCR only."""
from __future__ import annotations

from PIL import Image

from invoiceflow_ai.header_detection import detect_header, reconstruct_headers
from invoiceflow_ai.medical_payment_extractor import process_medical_payment
from invoiceflow_ai.pipeline import process_invoice


class OCRResult:
    def __init__(self, raw):
        self.json = {"res": raw}


class FakeOCR:
    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def predict(self, array):
        self.calls += 1
        return [OCRResult(self.raw)]


def boxes(rows):
    return {"rec_texts": [r[0] for r in rows],
            "rec_boxes": [[r[1]-12, r[2]-7, r[1]+12, r[2]+7] for r in rows],
            "rec_scores": [0.99] * len(rows)}


def payment_data():
    rows = [
        ("Nöi dung", 150, 46), ("Chi phi", 350, 45), ("BHYT", 445, 35),
        ("Só", 520, 35), ("BN", 610, 19),
    ]
    for stt, name, y, gross, insurance, patient in [
        (1, "Khám phụ khoa", 120, 100_000, 50_600, 49_400),
        (2, "Liqui-Prep Pap", 185, 368_500, 0, 368_500),
        (3, "Siêu âm tuyến vú hai bên", 250, 150_000, 58_600, 91_400),
    ]:
        rows += [(f"{stt} {name}", 130, y),
                 (f"(S.Luong: 1 Làn)", 130, y+18),
                 (f"{gross:,}", 350, y+3),
                 (f"{insurance:,}", 445, y+3),
                 ("0", 520, y+3),
                 (f"{patient:,}", 610, y+3)]
    rows.append(("Tông công:", 90, 335))
    return boxes(rows)


def test_package_headers_are_available():
    assert detect_header("Só tin") == "thanh_tien"
    assert detect_header("Tên thuc") == "ten_dich_vu"
    assert reconstruct_headers([], {"rec_texts": []}) == []


def test_payment_ocr_once_and_payment_is_not_invoice_total():
    ocr = FakeOCR(payment_data())
    result = process_invoice(Image.new("RGB", (700, 400)), ocr)
    assert result["document_type"] == "MEDICAL_PAYMENT"
    assert result["status"] == "EXTRACTED_PAYMENT"
    assert result["qa_status"] == "PENDING_HUMAN_REVIEW"
    assert result["ready_for_rpa"] is False
    assert result["ocr_inference_count"] == 1
    assert result["ocr_cache_hits"] == 1
    assert ocr.calls == 1
    assert len(result["items"]) == 3
    first = result["items"][0]
    assert first["so_luong"] == 1 and first["don_vi_tinh"] == "Lần"
    assert first["thanh_tien"] == 100000
    assert first["payment_details"]["benh_nhan_thanh_toan"] == 49400
    assert all(not item["validation_errors"] for item in result["items"])


def test_unknown_abstains_without_explicit_llm_permission():
    ocr = FakeOCR(boxes([("Other document", 100, 30)]))
    result = process_invoice(Image.new("RGB", (400, 400)), ocr)
    assert result["document_type"] == "UNKNOWN"
    assert result["status"] == "FALLBACK_NOT_AUTHORIZED"
    assert result["ready_for_rpa"] is False
    assert result["llm_inference_count"] == 0
    assert result["ocr_inference_count"] == 1
    assert ocr.calls == 1
