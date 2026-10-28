"""Command line for local, de-identified demonstration documents."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    p = argparse.ArgumentParser(prog="invoiceflow", description="InvoiceFlow AI local CLI")
    sub = p.add_subparsers(dest="command", required=True)
    infer = sub.add_parser("extract", help="Process one de-identified image locally")
    infer.add_argument("image", type=Path)
    infer.add_argument("--output", type=Path, help="Save result as JSON locally")
    infer.add_argument("--external-llm", action="store_true", help="Explicitly opt in to external Gemini for UNKNOWN OCR texts")
    sub.add_parser("serve", help="Start the FastAPI review backend")
    args = p.parse_args()
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit("Install base project dependencies first") from exc
        uvicorn.run("invoiceflow_ai.api:app", host="127.0.0.1", port=8000)
        return

    from PIL import Image, UnidentifiedImageError
    from .ocr_adapter import PaddleOCREngine
    from .pipeline import process_invoice
    from .gemini_adapter import create_gemini_generator
    if not args.image.is_file():
        p.error(f"File not found: {args.image}")
    try:
        img = Image.open(args.image).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        p.error(f"Invalid image: {exc}")
    if img.width * img.height > 16_000_000:
        p.error("Image exceeds 16 MP limit")
    gen = create_gemini_generator() if args.external_llm else None
    result = process_invoice(img, PaddleOCREngine(),
                             llm_generate=gen, allow_external_processing=args.external_llm)
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"Saved locally: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
