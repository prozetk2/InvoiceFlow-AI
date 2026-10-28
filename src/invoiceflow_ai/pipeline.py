"""Composition root: single OCR pass -> conservative routing -> KIE -> QA."""
from __future__ import annotations

from typing import Any, Callable

from .header_detection import detect_header, reconstruct_headers
from .llm_kie_fallback import process_document_with_fallback
from .medical_payment_extractor import process_medical_payment
from .medicine_table_extractor import process_medicine_final


def process_invoice(
    image: Any,
    ocr_engine: Any,
    *,
    llm_generate: Callable[[str], str | dict] | None = None,
    allow_external_processing: bool = False,
) -> dict:
    """Run one OCR prediction; LLM fallback is never enabled implicitly.

    Images should already be de-identified before any external LLM is enabled.
    No part of this pipeline can authorize RPA without an explicit backend review.
    """
    result = process_document_with_fallback(
        image,
        ocr_engine,
        medicine_handler=lambda im, eng: process_medicine_final(
            im, eng, detect_header=detect_header, reconstruct_headers=reconstruct_headers
        ),
        payment_handler=process_medical_payment,
        llm_generate=llm_generate,
        allow_external_processing=allow_external_processing,
    )
    result["ready_for_rpa"] = False
    return result
