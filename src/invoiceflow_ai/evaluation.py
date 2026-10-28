"""Reproducible evaluation harness: ground-truth labels vs pipeline predictions.

Implements the metrics defined in docs/EVALUATION.md. Ground truth is one
JSON file per document; predictions are pipeline results saved under the
same file stem. A document without a prediction file counts as a failure,
never as skipped. Reports carry counts, field names and STTs only, never
names or amounts, so a report can be shared without patient data.

Also provides an OCR replay engine, so a saved PaddleOCR result (a
"golden" OCR fixture) runs through the real routing and extraction code
offline, without downloading models.
"""
from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from numbers import Integral
from pathlib import Path
from typing import Any

from .validation import EXPORT_COLUMNS

HARNESS_VERSION = "1.0"
SUPPORTED_FAMILIES = ("MEDICINE_INVOICE", "MEDICAL_PAYMENT")
GT_FAMILIES = (*SUPPORTED_FAMILIES, "OTHER")
NUMERIC_FIELDS = ("stt", "so_luong", "don_gia", "thanh_tien")
TEXT_FIELDS = ("ten_dich_vu", "don_vi_tinh")
PAYMENT_FIELDS = ("chi_phi", "bhyt_chi", "so_giam", "benh_nhan_thanh_toan")
DISCLAIMER = ("Metrics describe only the labelled documents supplied to this run. "
              "Development examples are not held-out evidence; synthetic data says "
              "nothing about accuracy on real scans.")


# ---------------------------------------------------------------- loading

def _read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name}: expected a JSON object")  # noqa: TRY004 (bad input data)
    return data


def _check_gt(doc_id: str, gt: dict) -> None:
    if gt.get("document_family") not in GT_FAMILIES:
        raise ValueError(f"{doc_id}: document_family must be one of {', '.join(GT_FAMILIES)}")
    if not isinstance(gt.get("items", []), list):
        raise ValueError(f"{doc_id}: items must be a list")  # noqa: TRY004 (bad input data)
    for row in gt.get("items", []):
        missing = [c for c in EXPORT_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"{doc_id}: GT row is missing {', '.join(missing)}")


def load_pairs(gt_dir: Path, pred_dir: Path, *, split: str | None = None) -> list[dict]:
    """[{id, gt, pred | None}] for every GT file, optionally one split only."""
    gt_files = sorted(Path(gt_dir).glob("*.json"))
    if not gt_files:
        raise ValueError(f"No ground-truth JSON files in {gt_dir}")
    pairs = []
    for path in gt_files:
        gt = _read_json(path)
        _check_gt(path.stem, gt)
        if split is not None and gt.get("split") != split:
            continue
        pred_path = Path(pred_dir) / path.name
        pairs.append({"id": path.stem, "gt": gt,
                      "pred": _read_json(pred_path) if pred_path.is_file() else None})
    if not pairs:
        raise ValueError(f"No ground-truth documents in split {split!r}")
    return pairs


# ---------------------------------------------------------------- matching

def normalize_text(value: Any) -> str:
    """Case- and whitespace-insensitive, accents kept (exact_name_match)."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(value or ""))).strip().casefold()


def fold_accents(value: Any) -> str:
    """Accent-folded form, for the separate, weaker accent_folded_name_match."""
    s = unicodedata.normalize("NFD", normalize_text(value).replace("đ", "d"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _is_int(value: Any) -> bool:
    return isinstance(value, Integral) and not isinstance(value, bool)


def field_correct(field: str, gt_value: Any, pred_value: Any) -> bool:
    if field in TEXT_FIELDS:
        return pred_value is not None and normalize_text(gt_value) == normalize_text(pred_value)
    return _is_int(pred_value) and pred_value == gt_value


def _unique_stts(rows: list[dict]) -> bool:
    stts = [r.get("stt") for r in rows]
    return all(_is_int(s) for s in stts) and len(set(stts)) == len(stts)


def match_rows(gt_rows: list[dict], pred_rows: list[dict]) -> tuple[str, list[tuple[int, int]]]:
    """Pair GT and predicted rows: by STT when both sides have unique STTs, else by order."""
    if gt_rows and pred_rows and _unique_stts(gt_rows) and _unique_stts(pred_rows):
        by_stt = {r["stt"]: j for j, r in enumerate(pred_rows)}
        return "stt", [(i, by_stt[r["stt"]]) for i, r in enumerate(gt_rows) if r["stt"] in by_stt]
    return "order", [(i, i) for i in range(min(len(gt_rows), len(pred_rows)))]


# ---------------------------------------------------------------- metrics

def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo), 4)


def predicted_family(pred: dict | None) -> str:
    if pred is None:
        return "MISSING_PREDICTION"
    family = str(pred.get("document_type") or "UNKNOWN")
    return family if family in SUPPORTED_FAMILIES else "UNKNOWN"


def routing_correct(gt_family: str, pred_family: str) -> bool:
    # Abstaining on an unsupported document is the right answer, not an error.
    return pred_family == gt_family or (gt_family == "OTHER" and pred_family == "UNKNOWN")


def evaluate(pairs: list[dict], *, metadata: dict | None = None) -> dict:
    """Compute the docs/EVALUATION.md metrics over all pairs."""
    confusion: Counter = Counter()
    qa_statuses: Counter = Counter()
    warnings: Counter = Counter()
    total_checks: Counter = Counter()
    strategies: Counter = Counter()
    field_hits = Counter({f: 0 for f in EXPORT_COLUMNS})
    gt_rows = pred_rows = matched_rows = folded_name_hits = 0
    arithmetic_rows = arithmetic_ok = 0
    payment_fields = payment_hits = 0
    routed_supported = abstained = routing_ok = 0
    labelled_docs = exact_docs = 0
    ocr_calls = llm_calls = 0
    times: list[float] = []
    documents = []

    for pair in pairs:
        gt, pred = pair["gt"], pair["pred"]
        gt_family, pred_family = gt["document_family"], predicted_family(pred)
        confusion[(gt_family, pred_family)] += 1
        routed_supported += pred_family in SUPPORTED_FAMILIES
        abstained += pred_family in ("UNKNOWN", "MISSING_PREDICTION")
        routing_ok += routing_correct(gt_family, pred_family)

        truth = gt.get("items", [])
        predicted = [r for r in (pred or {}).get("items", []) if isinstance(r, dict)]
        strategy, pairs_ij = match_rows(truth, predicted)
        if truth:
            strategies[strategy] += 1
        gt_rows += len(truth)
        payment_fields += len(PAYMENT_FIELDS) * sum(isinstance(r.get("payment_details"), dict) for r in truth)
        pred_rows += len(predicted)
        matched_rows += len(pairs_ij)
        wrong_fields = []
        for i, j in pairs_ij:
            for field in EXPORT_COLUMNS:
                if field_correct(field, truth[i][field], predicted[j].get(field)):
                    field_hits[field] += 1
                else:
                    wrong_fields.append({"stt": truth[i]["stt"], "field": field})
            folded_name_hits += fold_accents(truth[i]["ten_dich_vu"]) == fold_accents(
                predicted[j].get("ten_dich_vu"))
            # Payment receipts carry four settlement amounts beyond the six export columns.
            gt_payment = truth[i].get("payment_details")
            if isinstance(gt_payment, dict):
                pred_payment = predicted[j].get("payment_details") or {}
                for field in PAYMENT_FIELDS:
                    if field_correct(field, gt_payment.get(field), pred_payment.get(field)):
                        payment_hits += 1
                    else:
                        wrong_fields.append({"stt": truth[i]["stt"], "field": f"payment_details.{field}"})
        for row in predicted:
            q, price, amount = (row.get(k) for k in ("so_luong", "don_gia", "thanh_tien"))
            if all(_is_int(v) for v in (q, price, amount)):
                arithmetic_rows += 1
                arithmetic_ok += q * price == amount
        matched_gt = {i for i, _ in pairs_ij}
        missed = [truth[i]["stt"] for i in range(len(truth)) if i not in matched_gt]
        extra = len(predicted) - len(pairs_ij)
        if truth:
            labelled_docs += 1
            exact_docs += not missed and not wrong_fields and extra == 0
        if pred is not None:
            qa_statuses[str(pred.get("qa_status") or "NONE")] += 1
            warnings.update(str(w) for w in pred.get("warnings", []) or [])
            for row in predicted:
                warnings.update(str(w) for w in row.get("warnings", []) or [])
            check = (pred.get("total_check") or {}).get("status") or (
                (pred.get("qa_summary") or {}).get("total_check_status"))
            if check:
                total_checks[check] += 1
            ocr_calls += int(pred.get("ocr_inference_count") or 0)
            llm_calls += int(pred.get("llm_inference_count") or 0)
            seconds = pred.get("pipeline_processing_time", pred.get("processing_time"))
            if isinstance(seconds, (int, float)):
                times.append(float(seconds))
        documents.append({
            "id": pair["id"], "gt_family": gt_family, "predicted_family": pred_family,
            "routing_correct": routing_correct(gt_family, pred_family),
            "qa_status": (pred or {}).get("qa_status"), "gt_rows": len(truth),
            "predicted_rows": len(predicted), "matched_rows": len(pairs_ij),
            "row_matching": strategy if truth else None, "missed_gt_stts": missed,
            "extra_predicted_rows": extra, "wrong_fields": wrong_fields,
        })

    total_docs = len(pairs)
    correct_fields = sum(field_hits.values())
    width = len(EXPORT_COLUMNS)
    return {
        "harness_version": HARNESS_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "metadata": dict(metadata or {}),
        "disclaimer": DISCLAIMER,
        "documents": {"total": total_docs,
                      "by_gt_family": dict(Counter(p["gt"]["document_family"] for p in pairs)),
                      "missing_predictions": sum(p["pred"] is None for p in pairs)},
        "routing": {
            "coverage": _rate(routed_supported, total_docs),
            "abstention_rate": _rate(abstained, total_docs),
            "routing_accuracy": _rate(routing_ok, total_docs),
            "confusion": [{"gt": g, "predicted": p, "count": n}
                          for (g, p), n in sorted(confusion.items())],
        },
        "extraction": {
            "gt_rows": gt_rows, "predicted_rows": pred_rows, "matched_rows": matched_rows,
            "row_matching": dict(strategies),
            "row_recall": _rate(matched_rows, gt_rows),
            "row_precision": _rate(matched_rows, pred_rows),
            "end_to_end_field_accuracy": _rate(correct_fields, gt_rows * width),
            "conditional_field_accuracy": _rate(correct_fields, matched_rows * width),
            "field_accuracy": {f: _rate(field_hits[f], gt_rows) for f in EXPORT_COLUMNS},
            "exact_name_match": _rate(field_hits["ten_dich_vu"], gt_rows),
            "accent_folded_name_match": _rate(folded_name_hits, gt_rows),
            "arithmetic_valid_rate": _rate(arithmetic_ok, arithmetic_rows),
            "payment_field_accuracy": _rate(payment_hits, payment_fields),
            "document_exact_match": _rate(exact_docs, labelled_docs),
        },
        "quality_gate": {"qa_status": dict(qa_statuses), "total_check": dict(total_checks),
                         "top_warnings": dict(warnings.most_common(15))},
        "operational": {"ocr_inference_count": ocr_calls, "llm_inference_count": llm_calls,
                        "processing_seconds_p50": _percentile(times, .5),
                        "processing_seconds_p95": _percentile(times, .95)},
        "per_document": documents,
    }


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def format_summary(report: dict) -> str:
    """Markdown summary of a report, for the terminal or a PR description."""
    r, e = report["routing"], report["extraction"]
    families = ", ".join(f"{k}={v}" for k, v in sorted(report["documents"]["by_gt_family"].items()))
    lines = [
        (f"Documents: {report['documents']['total']} ({families}); "
         f"missing predictions: {report['documents']['missing_predictions']}"),
        "",
        "| Metric | Value |",
        "| --- | --- |",
        f"| Routing accuracy | {_fmt(r['routing_accuracy'])} |",
        f"| Coverage (routed to a supported family) | {_fmt(r['coverage'])} |",
        f"| Abstention | {_fmt(r['abstention_rate'])} |",
        f"| Row recall ({e['matched_rows']}/{e['gt_rows']}) | {_fmt(e['row_recall'])} |",
        f"| Row precision ({e['matched_rows']}/{e['predicted_rows']}) | {_fmt(e['row_precision'])} |",
        f"| End-to-end field accuracy | {_fmt(e['end_to_end_field_accuracy'])} |",
        f"| Conditional field accuracy (matched rows only) | {_fmt(e['conditional_field_accuracy'])} |",
        f"| Exact name match | {_fmt(e['exact_name_match'])} |",
        f"| Accent-folded name match | {_fmt(e['accent_folded_name_match'])} |",
        f"| Payment settlement fields (end-to-end) | {_fmt(e['payment_field_accuracy'])} |",
        f"| Arithmetic valid (predicted rows with all numbers) | {_fmt(e['arithmetic_valid_rate'])} |",
        f"| Documents fully correct | {_fmt(e['document_exact_match'])} |",
        "",
        "Per-field accuracy: " + ", ".join(f"{k} {_fmt(v)}" for k, v in e["field_accuracy"].items()),
        "",
        f"_{report['disclaimer']}_",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- running the pipeline

class _ReplayResponse:
    def __init__(self, raw: dict):
        self.json = {"res": raw}


class ReplayOCREngine:
    """Serves a saved PaddleOCR `res` dict instead of running OCR."""

    def __init__(self, raw: dict):
        self.raw = {k: raw[k] for k in ("rec_texts", "rec_boxes", "rec_scores")}
        self.calls = 0

    def predict(self, image_array: Any) -> list[_ReplayResponse]:
        self.calls += 1
        return [_ReplayResponse(self.raw)]


def predict_from_ocr_fixtures(ocr_dir: Path, out_dir: Path,
                              process: Callable[[Any, Any], dict] | None = None) -> list[Path]:
    """Run the real pipeline on saved OCR fixtures; one prediction JSON per fixture.

    Each fixture holds PaddleOCR's `rec_texts`, `rec_boxes`, `rec_scores` and the
    page `image_size` [width, height]. Only OCR is replayed: routing, extraction
    and QA are the code under test.
    """
    from PIL import Image

    if process is None:
        from .pipeline import process_invoice
        process = process_invoice
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for path in sorted(Path(ocr_dir).glob("*.json")):
        fixture = _read_json(path)
        width, height = fixture["image_size"]
        result = process(Image.new("RGB", (int(width), int(height)), "white"), ReplayOCREngine(fixture))
        target = out_dir / path.name
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                          encoding="utf-8")
        written.append(target)
    return written


def parse_metadata(pairs: Iterable[str]) -> dict:
    """`key=value` strings -> dict, e.g. ocr_model=PP-OCRv5 split=dev."""
    metadata = {}
    for item in pairs:
        key, sep, value = item.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"Metadata must be key=value, got {item!r}")
        metadata[key.strip()] = value.strip()
    return metadata
