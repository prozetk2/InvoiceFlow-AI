"""Lazy, thread-serialized PaddleOCR v3 adapter.

    pip install -e '.[ocr]'
PaddleOCR performs its first model download at first initialization unless
model directories are specified and pre-populated.
"""
from __future__ import annotations

import os
import threading
from typing import Any


class PaddleOCREngine:
    def __init__(self, *, device: str | None = None, lang: str | None = None):
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("OCR extras missing: pip install -e '.[ocr]'") from exc
        options = {
            "lang": lang or os.getenv("INVOICEFLOW_OCR_LANG", "vi"),
            "device": device or os.getenv("INVOICEFLOW_OCR_DEVICE", "cpu"),
            "use_doc_orientation_classify": False,
            "use_doc_unwarping": False,
            "use_textline_orientation": False,
        }
        if os.getenv("INVOICEFLOW_OCR_VERSION"):
            options["ocr_version"] = os.environ["INVOICEFLOW_OCR_VERSION"]
        self._model = PaddleOCR(**options)
        self._lock = threading.Lock()

    def predict(self, image_array: Any) -> list[Any]:
        with self._lock:
            return self._model.predict(image_array)
