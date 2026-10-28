"""Opt-in Gemini *text-only OCR box* adapter. Never sends images.

WARNING: OCR text/boxes may still contain PHI/PII. The caller must apply its
own authorization and de-identification review before invoking this adapter.
"""
from __future__ import annotations

import os


def create_gemini_generator(*, model: str | None = None, api_key: str | None = None):
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise RuntimeError("Gemini extra missing: pip install -e '.[gemini]'") from exc
    secret = api_key or os.getenv("GEMINI_API_KEY")
    if not secret:
        raise RuntimeError("Set GEMINI_API_KEY securely (never commit it)")
    client = genai.Client(api_key=secret)
    model_id = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    def generate(prompt: str) -> str:
        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
        )
        if not response.text:
            raise RuntimeError("Gemini returned no text")
        return response.text

    return generate
