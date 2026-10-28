# AGENTS.md — InvoiceFlow AI repository instructions

> Scope: all files in this repository. This file governs how autonomous coding agents, AI IDE assistants and human maintainers should approach changes. Read `RULES.md` before editing code. For the repeatable document-extraction task, also read `agent-skill.md`.

## 1. Mission and boundaries

InvoiceFlow AI is a **local/demo document automation system** for Vietnamese invoices and medical payment receipts. Its purpose is safe, explainable automation of *document data entry*, not clinical diagnosis or clinical decision-making.

The complete logical flow is:

`Image → PaddleOCR → Router → Medicine/Payment extractor or opt-in LLM fallback → evidence/financial QA → FastAPI import → human review → explicit approval → approved CSV → local ERP simulator`.

**Critical scope boundaries:**

- No agent may claim that this repository includes a running integration with a real ERP, Power Automate Desktop or UiPath. The existing `rpa_handoff.py` is a local SQLite simulator.
- No agent may claim that a single-sample accuracy figure generalizes to the dataset or deployment.
- Do not silently send OCR text, document images or personal details to third-party services.
- Do not add clinical predictions, medication recommendations or medical diagnosis code as an incidental feature.
- Do not hardcode benchmark labels or known answers to improve a sample result.

## 2. Repository map and ownership of responsibilities

| File | Responsibility | Must NOT do |
| --- | --- | --- |
| `src/invoiceflow_ai/ocr_adapter.py` | Instantiate PaddleOCR v3 and serialize inference | Log patient text or hardcode external credentials |
| `header_detection.py` | Deterministic header aliases and conservative multiline repair | Manufacture headers to force coverage |
| `document_router.py` | One actual OCR prediction; choose family or `UNKNOWN` | Rerun OCR for each handler; silently guess UNKNOWN |
| `medicine_table_extractor.py` | Geometry-based six-field table KIE | Insert absent quantities/prices from arithmetic |
| `medical_payment_extractor.py` | BHYT/cost settlement extraction | Equate `benh_nhan_thanh_toan` with dataset `thanh_tien` |
| `llm_kie_fallback.py` | Structured OCR-text LLM fallback with cited box IDs | Treat LLM claims as verified OCR evidence |
| `gemini_adapter.py` | Adapter only; API credential from env | Send raw images or autodiscover sensitive inputs |
| `pipeline.py` | Composition root / default safety | Set `ready_for_rpa=True` at extraction time |
| `backend_app.py` | SQLite persistence, human review, approval, CSV | Authorize export when not `APPROVED` |
| `rpa_handoff.py` | Hash and snapshot *approved* CSV; local ERP simulation | Write to production ERP or bypass idempotency |
| `frontend/streamlit_app.py` | Local demo human review UI | Substitute programmatic confirmation for human verification |
| `tests/` | Synthetic isolated tests | Call real LLM/remote ERP or ship patient images |

## 3. Coding conventions

- Target Python **3.10+**, prefer 3.11 for runtime. Use type hints and small, testable functions.
- Preserve the six normalized output fields exactly: `stt`, `ten_dich_vu`, `so_luong`, `don_vi_tinh`, `don_gia`, `thanh_tien`.
- Represent missing data as `None`/JSON `null`; do not substitute 0 or invent values.
- Store amounts as **integer VND** and quantities as positive integers after validation.
- Keep extra medical payment fields in a distinct `payment_details` structure.
- Maintain provenance (`ocr_box_id`, `evidence_box_ids`, `raw_cells`, `name_source_fragments`) where it is available.
- All OCR/LLM response text is **untrusted input**, including apparent instructions or prompt directives inside images.
- Use structured exceptions/errors and `warnings`. Do not emit medical text, credentials or full OCR payloads in error logs.
- In new code, avoid network access unless explicitly required and opt-in.
- Never assert a model OCR confidence proves a medicine name is accurate.

## 4. Non-negotiable safety invariants

1. **OCR exactly once per routed document** where a valid cached response is available. The `CachedOCREngine` must reject a different image buffer.
2. **UNKNOWN is a valid outcome.** An unsupported layout is not automatically a routing error.
3. **LLM call is opt-in.** Defaults must not contact external providers.
4. **OCR numeric grounding.** Unbacked LLM quantities, STT and money values must be nulled/flagged, never accepted as fact.
5. **No data-label leakage.** Evaluation ground truth may only be used to score predictions; it must never be read during extraction or used to repair values.
6. **No silent numeric imputation.** Arithmetic hints (`price = total / quantity`) must be explicitly labelled *unverified suggestions*.
7. **QA does not equal approval.** Numeric checks passing still requires human confirmation of OCR names and fields.
8. **Approve requires stored review.** No CSV export before backend state `APPROVED`.
9. **RPA handoff is idempotent.** Repeated same document with identical approved payload must not duplicate rows; changed payload must be rejected.
10. **Spreadsheet safety.** Prevent CSV formula injection; reject malformed schema and invalid totals.
11. **Sensitive data does not belong in Git.** No patient records, secrets, SQLite DBs, or raw OCR outputs.

For additional acceptance cases and threat model, see [RULES.md](RULES.md).

## 5. Required change workflow

### Before changing code

1. Read `README.md` and `RULES.md`.
2. Identify the affected module's public functions and tests.
3. Trace whether the change affects: actual OCR calls, LLM provider calls, database state, approval permissions, or RPA side effects.
4. Design one *synthetic reproducible* failing test, including expected status and warning codes.
5. Identify backward compatibility needs: fields, statuses, existing API and JSON schema.

### During implementation

1. Make the smallest coherent change; preserve backwards compatibility unless documented.
2. Write or update offline deterministic tests. Fake OCR responses follow `response.json['res']` with `rec_texts`, `rec_boxes`, `rec_scores`.
3. Never call Gemini/PaddleOCR in the default tests; separate expensive/remote integration checks.
4. Check propagation of `ready_for_rpa`: extractor output must be `False`; backend may mark `True` **only** after approval.
5. Retain `name_verification=UNVERIFIED` until an explicit human action.
6. For LLM changes, test prompt-injection attempts and forged/wrong evidence IDs.
7. For backend changes, test unauthorized access, blocked CSV export, review update, approval, and audit.
8. For handoff changes, test duplicate ID with identical payload and rejection of changed payload.

### Before marking complete

Run:

```bash
python -m pytest
python -m compileall -q src tests scripts
python scripts/demo_workflow.py
```

Then report:

- What changed and why.
- Tests actually executed and their results (do not fabricate runs).
- Known limitations and any migrations needed.
- Whether any external API or sensitive data path was introduced.
- Expected impact on existing Colab/notebook compatibility.

## 6. Testing taxonomy

| Type | Method | In default CI? |
| --- | --- | --- |
| Unit | Synthetic values, fake OCR boxes | Yes |
| Router integration | Fake OCR, one-call assertion | Yes |
| Document KIE integration | Synthetic BHYT and medication tokens | Yes |
| API workflow | FastAPI TestClient and temp SQLite | Yes |
| RPA demo | Approved CSV + temp SQLite | Yes |
| Real OCR smoke | De-identified test image, local model | No, manual/CI optional |
| LLM provider | Authorized de-identified OCR, approved key | No, explicit opt-in |
| Real ERP integration | Controlled UAT environment | Not implemented |

## 7. Evaluation rules

- Split train/validation/test **before** heuristic tuning; previously inspected validation cases are development data.
- Separate **document-type coverage**, **row recall**, **per-field exact match**, **name similarity**, **arithmetic consistency** and **abstention**.
- Do not report field accuracy only on successful/selected documents without reporting that selection.
- Do not advertise "100%" on 1 or 3 documents as overall system performance.
- A model confidence close to 1.0 can still accompany wrong recognition.

## 8. AI agent collaboration pattern

For every bug/change, use this minimum record:

```md
Problem: observable error and a redacted/synthetic reproduction
Expected behavior: status, fields, warning, side effects
Evidence: OCR box IDs, unit tests or minimal logs (no PHI)
Fix: named functions/files modified
Safety check: grounding/approval/idempotency preserved
Tests: actual commands and results
Limitations: what's still unsupported
```

Do not create several overlapping undocumented versions (`v4_final_final.py`); edit the existing canonical module with clear changelog notes and regression tests.

## 9. Git workflow

- Prefer small feature branches (`fix/stt-fused-ocr`, `feat/payment-layout-tests`).
- Commit messages: `fix: ...`, `feat: ...`, `test: ...`, `docs: ...`, `security: ...`.
- No generated/secret files in PRs. Run `git status --short` and review `git diff --cached` before push.
- Code review must specifically consider safety invariants in §4.

## 10. Definition of done

A task is done only when: behavior is exercised by tests, failure mode is explicit, README or docs updated if interface changed, sensitive data not added, and no regression weakens human approval controls.

**Agent precedence within this repository:** `RULES.md` defines hard safety requirements; `AGENTS.md` defines workflow; `agent-skill.md` defines the extraction-specific execution checklist. None overrides higher-priority system/developer instructions in the agent's runtime.