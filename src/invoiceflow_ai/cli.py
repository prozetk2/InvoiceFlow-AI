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
    infer.add_argument("--save-ocr", type=Path,
                       help="Also save the raw OCR result as a replayable evaluation fixture (de-identified images only)")
    sub.add_parser("serve", help="Start the FastAPI review backend")
    replay = sub.add_parser("eval-predict", help="Run the pipeline on saved OCR fixtures (no OCR model needed)")
    replay.add_argument("ocr_dir", type=Path)
    replay.add_argument("--output", type=Path, required=True, help="Directory for one prediction JSON per fixture")
    score = sub.add_parser("eval", help="Score predictions against ground-truth labels")
    score.add_argument("gt_dir", type=Path)
    score.add_argument("pred_dir", type=Path)
    score.add_argument("--split", help="Only documents whose GT `split` equals this (e.g. test)")
    score.add_argument("--output", type=Path, help="Save the full JSON report")
    score.add_argument("--meta", nargs="*", default=[], metavar="KEY=VALUE",
                       help="Run metadata recorded in the report, e.g. ocr_model=PP-OCRv5")
    args = p.parse_args()
    if args.command == "eval-predict":
        from .evaluation import predict_from_ocr_fixtures
        written = predict_from_ocr_fixtures(args.ocr_dir, args.output)
        print(f"Wrote {len(written)} predictions to {args.output}")
        return
    if args.command == "eval":
        from .evaluation import evaluate, format_summary, load_pairs, parse_metadata
        try:
            report = evaluate(load_pairs(args.gt_dir, args.pred_dir, split=args.split),
                              metadata={**parse_metadata(args.meta), "split": args.split or "all"})
        except ValueError as exc:
            p.error(str(exc))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(format_summary(report))
        return
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
    engine = PaddleOCREngine()
    captured = {}
    if args.save_ocr:
        real_predict = engine.predict

        def predict(array):
            response = real_predict(array)
            captured.update(response[0].json["res"])
            return response
        engine.predict = predict
    result = process_invoice(img, engine,
                             llm_generate=gen, allow_external_processing=args.external_llm)
    if args.save_ocr:
        fixture = {"image_size": [img.width, img.height],
                   **{k: [v.tolist() if hasattr(v, "tolist") else v for v in captured.get(k, [])]
                      for k in ("rec_texts", "rec_boxes", "rec_scores")}}
        args.save_ocr.parent.mkdir(parents=True, exist_ok=True)
        args.save_ocr.write_text(json.dumps(fixture, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Saved OCR fixture locally: {args.save_ocr}")
    output = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
        print(f"Saved locally: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
