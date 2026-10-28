"""Evaluation harness: metric definitions, and a golden regression over the synthetic set."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from invoiceflow_ai.evaluation import (
    evaluate,
    format_summary,
    load_pairs,
    match_rows,
    parse_metadata,
    predict_from_ocr_fixtures,
)

ROOT = Path(__file__).resolve().parents[1]
SYNTHETIC = ROOT / "eval" / "synthetic"


def row(stt, name="Thuốc A", q=2, unit="Viên", price=100):
    return {"stt": stt, "ten_dich_vu": name, "so_luong": q, "don_vi_tinh": unit,
            "don_gia": price, "thanh_tien": q * price}


def pair(doc_id, family, gt_rows, pred):
    return {"id": doc_id, "gt": {"document_family": family, "items": gt_rows}, "pred": pred}


def test_missed_and_missing_documents_count_against_recall():
    pairs = [
        pair("a", "MEDICINE_INVOICE", [row(1), row(2)],
             {"document_type": "MEDICINE_INVOICE", "items": [row(1)]}),
        pair("b", "MEDICINE_INVOICE", [row(1)], None),  # no prediction file at all
    ]
    report = evaluate(pairs)
    e = report["extraction"]
    assert (e["gt_rows"], e["matched_rows"]) == (3, 1)
    assert e["row_recall"] == pytest.approx(1 / 3, abs=1e-4)
    assert e["end_to_end_field_accuracy"] == pytest.approx(6 / 18, abs=1e-4)
    assert e["conditional_field_accuracy"] == 1.0
    assert report["documents"]["missing_predictions"] == 1
    assert report["per_document"][0]["missed_gt_stts"] == [2]


def test_abstaining_on_other_is_correct_but_on_supported_is_not():
    pairs = [pair("o", "OTHER", [], {"document_type": "UNKNOWN", "items": []}),
             pair("m", "MEDICINE_INVOICE", [row(1)], {"document_type": "UNKNOWN", "items": []}),
             pair("p", "MEDICAL_PAYMENT", [], {"document_type": "MEDICINE_INVOICE", "items": []})]
    r = evaluate(pairs)["routing"]
    assert r["routing_accuracy"] == pytest.approx(1 / 3, abs=1e-4)
    assert r["abstention_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert r["coverage"] == pytest.approx(1 / 3, abs=1e-4)


def test_rows_match_by_stt_else_by_order():
    gt = [row(1), row(2), row(3)]
    strategy, pairs_ij = match_rows(gt, [row(3), row(1)])
    assert strategy == "stt" and pairs_ij == [(0, 1), (2, 0)]
    strategy, pairs_ij = match_rows(gt, [row(1), row(1)])  # duplicate STT: fall back
    assert strategy == "order" and pairs_ij == [(0, 0), (1, 1)]


def test_names_compare_case_and_space_insensitive_but_accent_sensitive():
    pred = {"document_type": "MEDICINE_INVOICE",
            "items": [row(1, name="  thuốc   a "), row(2, name="Thuoc B")]}
    e = evaluate([pair("a", "MEDICINE_INVOICE", [row(1), row(2, name="Thuốc B")], pred)])["extraction"]
    assert e["exact_name_match"] == 0.5
    assert e["accent_folded_name_match"] == 1.0


def test_report_never_contains_names_or_amounts():
    secret = "Nguyễn Văn Bệnh Nhân"
    pred = {"document_type": "MEDICINE_INVOICE", "items": [row(1, name="wrong", price=987654)]}
    report = evaluate([pair("a", "MEDICINE_INVOICE", [row(1, name=secret, price=123457)], pred)])
    text = json.dumps(report, ensure_ascii=False) + format_summary(report)
    assert secret not in text and "987654" not in text and "123457" not in text


def test_metadata_parsing():
    assert parse_metadata(["ocr_model=PP-OCRv5", "note=a=b"]) == {"ocr_model": "PP-OCRv5", "note": "a=b"}
    with pytest.raises(ValueError):
        parse_metadata(["no-equals-sign"])


def test_committed_synthetic_set_matches_generator(tmp_path):
    spec = importlib.util.spec_from_file_location("gen", ROOT / "scripts" / "make_synthetic_eval_set.py")
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    gen.build(tmp_path)
    for sub in ("gt", "ocr"):
        committed = {p.name: p.read_text(encoding="utf-8") for p in (SYNTHETIC / sub).glob("*.json")}
        fresh = {p.name: p.read_text(encoding="utf-8") for p in (tmp_path / sub).glob("*.json")}
        assert committed == fresh, f"Re-run scripts/make_synthetic_eval_set.py ({sub} is stale)"


def test_golden_regression_on_synthetic_set(tmp_path):
    """Pins the pipeline's behaviour on each named synthetic failure case.

    A change in these numbers is a behaviour change: update them only on purpose.
    """
    predict_from_ocr_fixtures(SYNTHETIC / "ocr", tmp_path)
    report = evaluate(load_pairs(SYNTHETIC / "gt", tmp_path))
    docs = {d["id"]: d for d in report["per_document"]}
    assert all(d["routing_correct"] for d in docs.values())
    assert docs["med_01_clean"]["wrong_fields"] == [] and docs["pay_06_clean"]["wrong_fields"] == []
    assert docs["med_02_name_typos"]["wrong_fields"] == [
        {"stt": 1, "field": "ten_dich_vu"}, {"stt": 3, "field": "ten_dich_vu"}]
    assert docs["med_03_missing_price"]["qa_status"] == "BLOCKED_VALIDATION_ERRORS"
    assert docs["med_04_dropped_row"]["missed_gt_stts"] == [3]
    assert docs["med_04_dropped_row"]["qa_status"] == "BLOCKED_TOTAL_MISMATCH"
    assert docs["med_05_broken_header"]["predicted_rows"] == 0
    assert docs["pay_07_bad_amount"]["wrong_fields"] == [
        {"stt": 2, "field": "payment_details.benh_nhan_thanh_toan"}]
    assert docs["other_08_lab_report"]["predicted_family"] == "UNKNOWN"
    e = report["extraction"]
    assert (e["gt_rows"], e["matched_rows"], e["predicted_rows"]) == (19, 16, 16)
    assert report["operational"]["ocr_inference_count"] == 8
    assert report["operational"]["llm_inference_count"] == 0
