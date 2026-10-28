"""Fast router unit+integration tests using mock OCR (no PaddleOCR model)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from PIL import Image
import numpy as np
from invoiceflow_ai.document_router import CachedOCREngine, classify_document, route_document
from invoiceflow_ai.medicine_table_extractor import process_medicine_final


def box(cx, cy, w=38, h=18):
    return [int(cx-w/2), int(cy-h/2), int(cx+w/2), int(cy+h/2)]


class Response:
    def __init__(self, rows):
        self.json = {"res": {"rec_texts": [r[0] for r in rows],
                              "rec_boxes": [r[1] for r in rows],
                              "rec_scores": [0.98]*len(rows)}}


class MockOCR:
    def __init__(self, rows):
        self.calls = 0
        self.response = Response(rows)
    def predict(self, _image):
        self.calls += 1
        return [self.response]


def detect(s):
    return {"stt":"stt", "tên thuc":"ten_dich_vu", "sl":"so_luong",
            "dvt":"don_vi_tinh", "don gia":"don_gia"}.get(s.lower())


def reconstruct(headers, raw):
    return headers


def med_handler(im, engine):
    return process_medicine_final(im, engine, detect_header=detect,
                                  reconstruct_headers=reconstruct)


def payment_handler(im, engine):
    got = engine.predict(np.array(im.convert("RGB")))[0]
    assert "rec_texts" in got.json["res"]
    return {"status": "EXTRACTED_PAYMENT", "qa_status": "PENDING_HUMAN_REVIEW",
            "items": [{"stt": 1, "service_name": "A"}], "ready_for_rpa": False}


def test_medicine():
    rows = [("STT", box(85,20,70,35)), ("Tên thuc",box(297,23,145,35)),
        ("SL",box(508,25,46,29)), ("DVT",box(597,25,78,30)),
        ("Don gia",box(708,23,121,34)), ("Só tin",box(844,19,111,38)),
        ("Levothyrox",box(241,67,120,18)), ("1",box(95,102,18,20)),
        ("50mcg",box(197,106,70,18)), ("68",box(508,106,22,18)),
        ("Viên",box(579,104,50,18)), ("1,520",box(733,102,67,18)),
        ("103,360",box(867,100,95,18)),
        ("(Levothyroxine)",box(281,142,140,16)),
        ("Han SD: 31/08/2027",box(540,181,160,14))]
    m=MockOCR(rows)
    result=route_document(Image.new("RGB",(920,210),"white"),m,
                          medicine_handler=med_handler,payment_handler=payment_handler)
    assert m.calls==1 and result["ocr_inference_count"]==1 and result["ocr_cache_hits"]==1
    assert result["document_type"]=="MEDICINE_INVOICE" and result["qa_status"]=="PENDING_HUMAN_REVIEW",result
    assert result["items"][0]["ten_dich_vu"]=="Levothyrox 50mcg (Levothyroxine)"
    assert result["ready_for_rpa"] is False
    print("PASS medicine: one real OCR, cached extractor OCR, extraction+QA")


def test_payment():
    rows=[("BN",box(612,18)),("BHYT",box(445,30)),("Só",box(524,32)),
          ("Thanh",box(612,42)),("Chi phi",box(354,46)),
          ("Nöi dung",box(154,47)),("chi",box(446,59)),("giám",box(525,62)),
          ("Toán",box(613,68)),("1 Khám phụ khoa",box(108,105)),
          ("100,000",box(355,118)),("50,600",box(450,119)),
          ("0",box(548,119)),("49,400",box(622,114))]
    m=MockOCR(rows)
    result=route_document(Image.new("RGB",(700,400),"white"),m,
                          medicine_handler=med_handler,payment_handler=payment_handler)
    assert m.calls==1 and result["ocr_cache_hits"]==1,result
    assert result["document_type"]=="MEDICAL_PAYMENT",result
    assert result["qa_status"]=="PENDING_HUMAN_REVIEW"
    print("PASS payment: one real OCR, cached payment handler")


def test_unknown():
    rows=[("dịch vụ",box(110,20)),("tổng cộng",box(160,200)),("1234",box(380,220))]
    m=MockOCR(rows)
    result=route_document(Image.new("RGB",(700,400),"white"),m,
                          medicine_handler=med_handler,payment_handler=payment_handler)
    assert m.calls==1 and result["ocr_cache_hits"]==0
    assert result["document_type"]=="UNKNOWN" and result["status"]=="UNSUPPORTED_LAYOUT",result
    assert result["ready_for_rpa"] is False
    print("PASS unknown: abstention rather than misrouting")


def test_ambiguous():
    rows=[("STT",box(80,20)),("Tên thuc",box(200,20)),("SL",box(300,20)),
          ("DVT",box(350,20)),("BN",box(500,20)),("Chi phi",box(450,20)),
          ("BHYT",box(600,20))]
    decision=classify_document(Response(rows).json["res"], 200)
    assert decision["document_type"]=="UNKNOWN"
    assert decision["reason"]=="AMBIGUOUS_MULTIPLE_LAYOUTS"
    print("PASS ambiguous: conservative abstention")


def test_cache_guard():
    a=np.zeros((10,10,3),dtype=np.uint8)
    cache=CachedOCREngine(a,Response([]))
    assert cache.predict(a)
    try:
        cache.predict(np.ones((10,10,3),dtype=np.uint8))
        raise AssertionError("Cross-document cache reuse should be rejected")
    except ValueError:
        pass
    print("PASS cache: rejects image mismatch")


if __name__ == "__main__":
    test_medicine(); test_payment(); test_unknown(); test_ambiguous(); test_cache_guard()
    print("ALL DOCUMENT ROUTER TESTS PASSED")
