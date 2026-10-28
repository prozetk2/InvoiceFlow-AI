# Reproducible evaluation plan

## Data integrity

- Use datasets with documented terms and permission to process. Never commit the medical scans, personal identifiers, or proprietary ground truth.
- Define document-family labels independently of a router's prediction: `MEDICINE_INVOICE`, `MEDICAL_PAYMENT`, `OTHER`.
- Split before tuning into train/dev/held-out test. Previously debugged cases (`val_ds[0]`, `[1]`, `[2]`, `[3]`, etc.) are **development examples**, not clean hold-out test evidence.
- Do not fill extraction fields from labels, arithmetic, item order or external drug catalogs before scoring.

## Metrics

### Routing

Document coverage = routed to supported family / total documents. Abstention = UNKNOWN / total. Routing accuracy = correct family decisions / independently labeled cases. An abstained OTHER example is not a misclassification.

### Field extraction

- `GT_rows`: all labeled line items across **all** test documents, including failed/UNKNOWN documents.
- `matched_rows`: unique correct matches under the stated matching strategy. STT can serve as anchor only if both GT and prediction contain reliable unique STT.
- `row_recall = matched_rows / GT_rows`.
- `end_to_end_field_accuracy = correct_fields / total_labeled_fields` across all GT rows, including missed rows/documents as incorrect.
- Also optionally report `conditional_field_accuracy` **among matched rows only**, explicitly labeled conditional.
- `exact_name_match`: case/whitespace-normalized string equality. Optional accent-folded or edit-distance similarity is a separate metric.
- `arithmetic_valid_rate`: among rows where numbers are present, their stated equation balances. This does **not** prove source fidelity.

### Operational

- Real OCR inference count, LLM inference count, pixel cache hits.
- Wall clock time per page and p50/p95 across dataset.
- Human review count and proportion; number of invalid/ambiguous fields.
- Backend failure rate, approval wait duration, handoff duplicates/errors.

## Example of correct reporting

> On two **selected development** medicine tables: 7/7 rows were detected; 2 rows were missing at least one numeric value, and 5 had complete arithmetic. This does not estimate held-out test performance.

> On one **selected development** 3-row LLM fallback: the repair targeted fused STT and unit OCR, producing an expected 17/18 exact fields under row-order matching. The number is not dataset-wide accuracy.

## Error taxonomy

| Category | Example | Correct response |
| --- | --- | --- |
| OCR recognition | `Vién`, `15 83ó`, `CAPSUL.ES` | Preserve source; alias only well-defined units; name review |
| Document routing | Payment header absent | UNKNOWN / abstain |
| Header reconstruction | Split `Tên` / `thuốc` | Merge only when geometrically supported |
| Row alignment | `15` quantity assigned to next row | Test ordered monotonic row match |
| LLM hallucination | STT 2 absent from OCR | Null + `STT_NOT_GROUNDED` |
| Arithmetic inconsistency | 30 × 4,950 ≠ listed total | `AMOUNT_MISMATCH`, review |
| Human review entry | Literal `REVIEW` used as name | Reject placeholder, require correction |
| Workflow misuse | Export while PENDING_REVIEW | HTTP 409 |

## Running the harness

The metrics above are implemented in `src/invoiceflow_ai/evaluation.py` and exposed by the CLI.

**Inputs.** One ground-truth JSON per document, plus one prediction JSON (a saved pipeline result) under the same file name:

```json
{"document_family": "MEDICINE_INVOICE", "split": "test",
 "items": [{"stt": 1, "ten_dich_vu": "...", "so_luong": 2, "don_vi_tinh": "Viên", "don_gia": 1500, "thanh_tien": 3000}]}
```

`document_family` is `MEDICINE_INVOICE`, `MEDICAL_PAYMENT` or `OTHER`; payment rows may add `payment_details`. A GT file with no prediction counts as a failed document.

**Scoring.**

```bash
invoiceflow eval GT_DIR PRED_DIR --split test --output report.json --meta ocr_model=PP-OCRv5 dataset=v1
```

Rows are matched by STT when both sides have unique STTs, otherwise by order; the report states which. The JSON report has per-document results (missed STTs, wrong field names) but never names or amounts, so it can be shared without patient data.

**Producing predictions without re-running OCR.** `invoiceflow extract IMAGE --output pred/x.json --save-ocr ocr/x.json` saves the raw OCR next to the result. `invoiceflow eval-predict ocr/ --output pred/` later replays those OCR fixtures through the current routing, extraction and QA code, so extractor changes can be compared on the same OCR in seconds, offline.

**Synthetic set.** `eval/synthetic/` holds eight generated documents (`scripts/make_synthetic_eval_set.py`), each injecting one named failure: OCR name typo, missing unit price, dropped row (caught by the document total check), unreadable header, misread payment amount, and an unsupported document that must abstain. `make eval` runs the full loop, and `tests/test_evaluation.py` pins the per-document outcomes so a behaviour change fails CI. These numbers exercise the code paths only; they are **not** accuracy estimates for real scans.

## Offline regression

```bash
python -m pytest
```

These tests verify invariants with synthetic fixtures, not optical recognition accuracy on real images. Full OCR and LLM evaluation must be performed separately, on appropriately authorized/test datasets, and disclose model/provider versions and any data preprocessing.
