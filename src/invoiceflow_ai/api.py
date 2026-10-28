"""ASGI entry point.

Default: review-only backend; upload returns 503 until OCR is explicitly enabled.
Never auto-send medical data to an external LLM via the HTTP upload endpoint.
"""
from __future__ import annotations

import os
from functools import lru_cache

from .backend_app import create_app


def _enabled(env_name: str) -> bool:
    return os.getenv(env_name, "false").strip().lower() == "true"


@lru_cache(maxsize=1)
def get_ocr():
    from .ocr_adapter import PaddleOCREngine
    return PaddleOCREngine()


def create_server():
    app = create_app()
    if _enabled("INVOICEFLOW_ENABLE_OCR_UPLOAD"):
        from .pipeline import process_invoice
        engine = get_ocr()
        # Deliberately no LLM callback: HTTP upload never automatically
        # transmits OCR text/medical records to a third party.
        app.state.document_processor = lambda image: process_invoice(image, engine)
    return app


app = create_server()
