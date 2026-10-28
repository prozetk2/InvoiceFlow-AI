"""InvoiceFlow AI — Module 03: OCR-once conservative document router.

The router classifies the OCR header region and passes the *same* OCR result to
one selected extractor through CachedOCREngine. This prevents redundant OCR.

Supported document families (conservative baseline):
* MEDICINE_INVOICE: STT-led six-column medication/service table.
* MEDICAL_PAYMENT: BHYT / cost / BN settlement table.

Uncertain/unknown evidence is sent to human review; this module does NOT run
an LLM or upload medical records. Detection rules are baseline heuristics,
not a trained document-type classifier.
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Callable, Any

import numpy as np

MED_FIELDS = frozenset({"stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien"})
PAYMENT_KEYS = frozenset({"chi_phi", "bhyt", "bn"})


def _fold(text: Any) -> str:
    s = unicodedata.normalize("NFD", str(text).casefold().replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _compact(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def _medicine_key(text: str) -> str | None:
    s = _compact(text)
    # Only explicit table-header aliases. Don't infer fields from row values.
    aliases = {
        "stt": "stt", "sott": "stt", "sothutu": "stt",
        "tenthuc": "ten_dich_vu", "tenthu": "ten_dich_vu",
        "tenthuoc": "ten_dich_vu", "tendichvu": "ten_dich_vu",
        "tenhang": "ten_dich_vu", "tenvattu": "ten_dich_vu",
        "sl": "so_luong", "soluong": "so_luong", "luong": "so_luong",
        "dvt": "don_vi_tinh", "donvitinh": "don_vi_tinh",
        "dongia": "don_gia", "dongla": "don_gia",
        "thanhtien": "thanh_tien", "thanhtin": "thanh_tien", "thnhtin": "thanh_tien",
        "sotien": "thanh_tien", "sotin": "thanh_tien",
    }
    return aliases.get(s)


def _payment_key(text: str) -> str | None:
    s = _compact(text)
    aliases = {
        "chiphi": "chi_phi", "chiphithuchien": "chi_phi",
        "bhyt": "bhyt", "baohiemyte": "bhyt",
        "bn": "bn", "benhnhan": "bn",
        "sogiam": "so_giam", "giam": "so_giam", "noidung": "noi_dung",
        "thanhtoan": "thanh_toan",
    }
    return aliases.get(s)


class CachedOCREngine:
    """Serve one precomputed OCR result to an extractor without running OCR.

    The extractor's .predict() input must be pixel-identical to the routed
    document. This avoids accidentally reusing another patient's OCR result.
    """
    def __init__(self, image_array: np.ndarray, response: Any):
        self._image = np.asarray(image_array)
        self._response = response
        self.cache_hits = 0

    def predict(self, image_array: Any) -> list[Any]:
        arr = np.asarray(image_array)
        if arr.shape != self._image.shape or not np.array_equal(arr, self._image):
            raise ValueError("Cached OCR requested for a different image; refusing unsafe reuse")
        self.cache_hits += 1
        return [self._response]


def classify_document(raw: dict, height: int) -> dict:
    """Classify using OCR header tokens only; abstain rather than guess.

    Header recognition is intentionally conservative. It will miss some novel
    layouts, which can later be handled by a reviewed/fallback workflow.
    """
    text_list = raw.get("rec_texts", [])
    box_list = raw.get("rec_boxes", [])
    if not text_list or len(text_list) != len(box_list):
        return {
            "document_type": "UNKNOWN", "reason": "OCR_EMPTY_OR_MALFORMED",
            "medicine_evidence": [], "payment_evidence": [],
        }

    medicine = set()
    payment = set()
    for text, box in zip(text_list, box_list):
        try:
            center_y = (float(box[1]) + float(box[3])) / 2.0
        except (IndexError, TypeError, ValueError):
            continue
        if center_y > height * .35:
            continue
        mk = _medicine_key(text)
        pk = _payment_key(text)
        if mk:
            medicine.add(mk)
        if pk:
            payment.add(pk)

    # Strict payment signature from the existing BHYT document: Chi phi+BHYT+BN.
    payment_match = PAYMENT_KEYS.issubset(payment)
    # Can be a multiline header, so the name header may be reconstructed later.
    medicine_match = ("stt" in medicine and len(medicine) >= 4 and
                      len(medicine.intersection({"so_luong", "don_vi_tinh", "don_gia", "thanh_tien"})) >= 2)

    if payment_match and medicine_match:
        doc_type, reason = "UNKNOWN", "AMBIGUOUS_MULTIPLE_LAYOUTS"
    elif payment_match:
        doc_type, reason = "MEDICAL_PAYMENT", "PAYMENT_HEADER_SIGNATURE"
    elif medicine_match:
        doc_type, reason = "MEDICINE_INVOICE", "MEDICINE_HEADER_SIGNATURE"
    else:
        doc_type, reason = "UNKNOWN", "INSUFFICIENT_ROUTING_EVIDENCE"

    return {
        "document_type": doc_type, "reason": reason,
        "medicine_evidence": sorted(medicine),
        "payment_evidence": sorted(payment),
    }


def route_document(
    image: Any,
    ocr_engine: Any,
    *,
    medicine_handler: Callable[[Any, Any], dict],
    payment_handler: Callable[[Any, Any], dict],
) -> dict:
    """OCR -> route -> selected extractor+QA; one *actual* OCR prediction.

    Both handlers accept (PIL_image, ocr_like_engine) and return dictionaries.
    For Colab:
       medicine_handler=lambda im, eng: process_medicine_final(
           im, eng, detect_header=detect_header, reconstruct_headers=reconstruct_headers)
       payment_handler=lambda im, eng: finalize_medical_payment(
           extract_medical_payment_v2(im, eng))

    `ready_for_rpa` is forced False. A future audited human-approval workflow
    must make a separate decision to release data to RPA.
    """
    start = time.perf_counter()
    rgb = np.asarray(image.convert("RGB"))
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("Expected a valid RGB image")
    height = rgb.shape[0]

    response = ocr_engine.predict(rgb)[0]
    raw = response.json["res"]
    routing = classify_document(raw, height)

    result = {
        "status": "UNSUPPORTED_LAYOUT",
        "document_type": routing["document_type"],
        "qa_status": "BLOCKED_UNSUPPORTED_LAYOUT",
        "ready_for_rpa": False,
        "items": [],
        "warnings": [routing["reason"]],
    }
    cache = CachedOCREngine(rgb, response)
    if routing["document_type"] == "MEDICINE_INVOICE":
        result = medicine_handler(image, cache)
    elif routing["document_type"] == "MEDICAL_PAYMENT":
        result = payment_handler(image, cache)

    if not isinstance(result, dict):
        raise TypeError("Extractor handler must return a dict")

    result = dict(result)  # Do not mutate handler's reference
    # Prevent a handler from silently changing route identity.
    result["document_type"] = routing["document_type"]
    result["ready_for_rpa"] = False
    result["routing"] = routing
    result["ocr_inference_count"] = 1
    result["ocr_cache_hits"] = cache.cache_hits
    result["pipeline_processing_time"] = round(time.perf_counter() - start, 3)
    return result
