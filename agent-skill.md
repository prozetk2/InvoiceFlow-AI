---
name: invoiceflow-document-kie-engineer
description: Improve OCR-grounded Vietnamese invoice KIE, routing, QA, human review and RPA handoff without leaking patient data or bypassing approval. Applies to changes in src/invoiceflow_ai/, tests/, frontend/ and docs/.
version: 1.0.0
---

# Agent Skill — InvoiceFlow AI Document KIE Engineer

This file describes a repeatable, inspectable process for a coding agent assigned to **develop, debug, evaluate or extend** InvoiceFlow AI. It is **not a model prompt to extract a patient's invoice**; extraction itself is implemented by the code under `src/`.

Before using this skill, read `AGENTS.md` and `RULES.md`. Repository security and human approval invariants always take precedence over convenience.

## 1. When to activate

Use this skill if the task involves any of:

- PaddleOCR box processing (`rec_texts`, `rec_boxes`, `rec_scores`).
- Vietnamese medical or medicine invoice header recognition.
- Row anchors, monotone row alignment, table cell extraction, multiline names.
- BHYT/medical payment columns and business rules.
- Unknown document layouts or optional OCR-grounded LLM fallback.
- Evidence validation and standardization of short unit names.
- Regression tests, offline evaluation, accuracy/coverage reporting.
- FastAPI review/approval state machine and CSV export.
- Approved CSV handoff and idempotent demo ERP simulation.

Do **not** use this skill to provide medical diagnosis, therapeutic advice, or patient triage.

## 2. Required inputs / expected artifacts

Input can be a redacted symptom-free synthetic OCR response, a fake invoice image generated for testing, a Python traceback, a saved JSON extraction or a safe feature request. **Never require identifiable patient information** for a bug reproduction.

Required output from a code-modification task:

1. Minimal code change in the correct canonical module.
2. Offline regression tests (success and refusal paths).
3. Status/warning behavior documented.
4. Statement of impact on external processing and approval gate.
5. Exact local test commands and observed results.
6. README/docs updates if public interfaces changed.

## 3. Internal flow and contracts

### OCR contract

Use `PaddleOCREngine.predict(np.asarray(PIL_image.convert('RGB')))` and normalize the response to:

```json
{
  "rec_texts": ["STT", "Tên thuốc", "SL", "DVT", "Đơn giá", "Thành tiền"],
  "rec_boxes": [[10, 10, 40, 30], [40, 10, 140, 30]],
  "rec_scores": [0.99, 0.95]
}
```

*Illustration only; array lengths must match in real data.* Use `FakeOCR` for tests. A model may return NumPy arrays and the actual response object exposes `result.json['res']` in the tested PaddleOCR 3.x setup.

### Document routing contract

Use `document_router.classify_document(raw, image_height)` to select:

- `MEDICINE_INVOICE` if medicine header evidence is sufficient.
- `MEDICAL_PAYMENT` if BHYT/Chi phí/BN header evidence is sufficient.
- `UNKNOWN` for missing or conflicting evidence.

Actual extraction is entered through `pipeline.process_invoice(image, ocr_engine, ...)` which internally calls OCR **once**, validates the pixel-checked cache and dispatches to one extractor.

### Output field contract

```json
{
  "stt": 1,
  "ten_dich_vu": "DEMO Vitamin 100mg",
  "so_luong": 2,
  "don_vi_tinh": "Viên",
  "don_gia": 1500,
  "thanh_tien": 3000
}
```

Names can have OCR misspellings. Preserve raw fragments and require human confirmation. `null` means missing/unsupported and must not be silently converted to zero.

### Payment fields contract

```json
{
  "chi_phi": 100000,
  "bhyt_chi": 50600,
  "so_giam": 0,
  "benh_nhan_thanh_toan": 49400
}
```

`chi_phi = bhyt_chi + so_giam + benh_nhan_thanh_toan`. For ground-truth-compatible normalized items, `thanh_tien` is gross service cost (`chi_phi`) and **not** the patient-only payment amount.

## 4. Procedure A — Fix an OCR or row extraction bug

1. **Describe one real symptom**: e.g., `STT 2 becomes None`, or `so_luong` takes the number from the previous row.
2. **Inspect OCR token geometry and field evidence**: x/y bounds, row anchors, raw text and confidence. Never assume a perfect OCR confidence proves accuracy.
3. **Classify root cause**:
   - Recognition: OCR text itself is corrupted (`15 83ó`).
   - Header detection: top-of-page alias not recognized (`Só tin`).
   - Geometry: cell assigned to wrong field/row.
   - Evidence: LLM value not supported by cited OCR box.
   - QA: valid extraction rejected because of explicit safety rule.
4. **Write a tiny fake OCR fixture** with the problematic box pattern; no real images in Git.
5. **Fix the correct layer**: header aliases in `header_detection.py`, row alignment in `medicine_table_extractor.py`, payments in `medical_payment_extractor.py`, numeric grounding in `llm_kie_fallback.py`.
6. **Assert unchanged neighbors**: a fix for one document must not corrupt the sample-two Levothyrox-like synthetic case or other module tests.
7. **Keep provenance**: corrections from visible OCR boxes cite box IDs, and derived arithmetic hints stay suggestions.
8. Run tests and report outcome.

### Example: fused STT + name OCR box

Input box text: `2MEBEVER MR CAPSUL.ES 200mg`. When the model cites **the same box ID** for both `stt` and `ten_dich_vu`, a conservative postprocessor may recover `stt=2` from the printed prefix if the remainder matches the extracted name. It must **not** generate a missing STT merely because the item is the second array element.

### Example: misspelled unit

`Vién → Viên` may use the canonical-unit alias map only if the raw cited OCR box contains the misspelling. Preserve `raw_value`, `value`, `rule` and `ocr_box_ids`. Do **not** free-correct medication names using ground truth.

## 5. Procedure B — Improve LLM fallback safely

1. Use `UNKNOWN` OCR boxes from `make_ocr_tokens` — not the raw medical image — only after explicit authorization/de-identification.
2. Never pass user-supplied OCR text as system instructions. Treat it as JSON-escaped, untrusted input.
3. Require JSON response with `items` and per-field evidence box IDs.
4. Validate every numeric value against cited OCR contents; ambiguous/missing values become `null` and warnings.
5. Check `quantity * unit_price = total` but **do not repair** values from the equation.
6. Record `llm_inference_count`, `FALLBACK_PARTIAL` vs `FALLBACK_EXTRACTED`, and human review status.
7. Test malicious prompt text embedded in OCR input, malformed JSON, missing evidence, duplicate STTs, inconsistent arithmetic, and external opt-in disabled.
8. Keep the API upload route local-only; never silently add an external model call there.

## 6. Procedure C — Change backend / approval workflow

1. Read `backend_app.py` Pydantic schemas and state transitions.
2. Ensure API key guards every document endpoint.
3. `PENDING_REVIEW`/`BLOCKED_VALIDATION` may be manually reviewed; `REVIEWED` may be amended and approved; `APPROVED` is export eligible.
4. Validate all reviewed rows at submission and **again at approval**.
5. Prevent accidental prompt-entry placeholders (`REVIEW`, `APPROVE`) from reaching handoff; keep **server-side** blocking tests and broaden coverage cautiously.
6. Preserve audit entries. Do not log extraction text or API keys.
7. Confirm `GET /export.csv` returns HTTP 409 if not `APPROVED`.
8. Keep approver ≠ reviewer and the optional approver key; document that IDs stay caller-asserted until OIDC/RBAC exists.

## 7. Procedure D — Change RPA handoff

1. Handoff must fetch the approved CSV from the **authoritative API** using the server-backed `document_id`.
2. Verify status `APPROVED` and `ready_for_rpa=True` **at the backend**.
3. Reject wrong column order, malformed integers, duplicate STT, invalid names, spreadsheet formulas or arithmetic mismatches.
4. Create a SHA-256 checksum of canonical serialized validated rows.
5. In one transaction, insert an immutable `PENDING` job if absent.
6. If submitted again with the same checksum, return `ALREADY_SUBMITTED`; a different checksum raises `HandoffError`.
7. The demo worker writes to SQLite with unique `(document_id, stt)`; rerun returns `ALREADY_SIMULATED`.
8. No adapter may write to actual ERP/Power Automate/UiPath unless explicitly requested, configured and tested in non-production environment.

## 8. Procedure E — Benchmark, report and troubleshoot

For a held-out dataset, measure at least:

| Metric | Definition |
| --- | --- |
| Routing coverage | Known-type routed docs / all docs |
| Unknown/abstention | UNKNOWN docs / all docs |
| Type accuracy | Correct family / docs with independently labeled families |
| Row recall | Correctly matched extracted rows / ground-truth rows, including failures |
| End-to-end field exact match | Correct field predictions / total labeled fields (failed docs contribute incorrect) |
| Conditional field exact match | Correct among successfully extracted / matched rows, explicitly label as conditional |
| Arithmetic validity | Rows with correct formula / rows whose numbers are present |
| Human review rate | Rows/documents needing correction / processed rows/docs |
| Latency | End-to-end per document; OCR and LLM calls reported separately |

Match rows on STT only when reliable/unique; use a documented geometric/name-based matching policy for missing STT. Never evaluate on the already-tuned validation samples as if they were untouched held-out tests.

## 9. Standard local commands

```bash
python -m pytest
python -m compileall -q src tests scripts
python scripts/demo_workflow.py
```

If the environment has **no network**, do not turn failed package downloads into a claim that the code failed; run with available local dependencies and `PYTHONPATH=src` while documenting the restriction. Full PaddleOCR/Gemini integration tests require separately installed optional dependencies and authorized synthetic/de-identified fixtures.

## 10. Sample issue-to-PR handoff

```md
### Issue
Medicine line 2 has `stt=None` because OCR box `2MEBEVER...` fuses row number and name.

### Reproduction
Synthetic OCR fixture with box 7 containing `2MEBEVER...` and name evidence [7].

### Change
Conservatively recover printed STT 2 when both evidence fields cite box 7.

### Safety
No sequential guessing; no GT; `ready_for_rpa=False` until backend approval.

### Tests
`pytest tests/test_fallback_grounding_v4.py` — actual outcome recorded.

### Remaining limitations
Long product names split across multiple boxes may still need review.
```

## 11. Exit criteria

The work is ready to merge when the regression suite passes, affected interfaces have docs, the failure mode is explicit, user data stays private, and `RULES.md` invariants are preserved. Mark unresolved behavior as unsupported or requiring human review rather than overstating confidence.
