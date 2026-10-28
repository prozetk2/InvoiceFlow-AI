# Results and demo evidence

The main [`README.md`](../README.md#results--demo-evidence) displays four illustrated evidence cards. They were **reconstructed from observed outputs**, rather than captured from a running production UI. This page records their provenance and limits so the repository can be cited responsibly in a portfolio.

## Colab run: reviewed medicine extraction (2026-10-27, UTC)

| STT | Reviewed `ten_dich_vu` | `so_luong` | `don_vi_tinh` | `don_gia` | `thanh_tien` |
| --- | --- | ---: | --- | ---: | ---: |
| 1 | NEWBUTIN SR 300mg | 56 | Viên | 6955 | 389480 |
| 2 | MEBEVER MR CAPSULES 200mg | 56 | Viên | 3795 | 212520 |
| 3 | NORMAGUT 250mg | 56 | Viên | 6955 | 389480 |

All three arithmetic checks `so_luong * don_gia == thanh_tien` were true. Their sum is `991480` VND. The model initially produced `MEBEVER MR CAPSUL.ES 200mg`; an accidental operator input `REVIEW` was caught in the interactive workflow and a subsequent review corrected that field. **The reviewed values were confirmed by the operator, not automatically inferred to be ground truth.**

Backend response log, as reported in the interactive run:

```text
Import:         status=PENDING_REVIEW
Human review:   HTTP 200, status=REVIEWED
Correction:     HTTP 200, status=REVIEWED
Approval:       HTTP 200, status=APPROVED, ready_for_rpa=True
CSV export:     HTTP 200, 3 rows
Preapproval export: HTTP 409 (blocked)
```

## Colab run: RPA simulation

```text
submit_approved_job: state=PENDING, row_count=3, action=CREATED
run_demo_worker:    state=SIMULATED, row_count=3, action=SIMULATED_NOW
second worker run:  action=ALREADY_SIMULATED
ERP simulator:      3 rows before and after retry
Audit events:       HANDOFF_ACCEPTED, LOCAL_ERP_SIMULATED
Payload SHA-256:    2432fa330a41dd97c64a58ae335937641a4f5236998fd55ea04cae02644e7251
```

The demo worker only writes **local SQLite rows**. Do not describe this as running an automation on a real hospital/ERP interface.

## Package validation

The source consolidation recorded `41` offline automated tests passing and a **separate, fully synthetic** end-to-end demonstration. Its test fixtures are deterministic and should not be interpreted as dataset-level model performance. You can reproduce the current results via:

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python scripts/demo_workflow.py
```

Read [TEST_REPORT.md](../TEST_REPORT.md) for the original packaging check and [EVALUATION.md](EVALUATION.md) for a proper held-out benchmarking plan.

## Images and provenance

| Asset | Basis | Limit |
| --- | --- | --- |
| `assets/results/01-extraction-review.png` | Human-reviewed 3-row Colab output | Not a raw OCR screenshot |
| `assets/results/02-approval-export.png` | Reported FastAPI HTTP status and transitions | Demo endpoint, not production authorization |
| `assets/results/03-rpa-handoff.png` | Colab RPA simulator output and audit events | No actual ERP integration |
| `assets/results/04-test-evidence.png` | Repository validation report | Offline tests, no real OCR/Gemini/API benchmarking |

The images are generated deterministically by `scripts/render_demo_results.py` using Pillow. They contain no original invoices, patient photos, patient identifiers or access tokens.

## What remains to validate

- Full data evaluation on a held-out set (document-level and field-level precision/recall, including OCR errors).
- Device/OS-specific PaddleOCR initialization and real inference for anonymized receipts.
- Authorized external Gemini calls and evidence validation on independent data.
- Authentication/authorization separation, encrypted storage and production security.
- Real UiPath, Power Automate Desktop, or third-party ERP integration.

## Suggested portfolio phrasing

> Built and validated a modular Vietnamese invoice-processing prototype with OCR-guided extraction, optional LLM fallback, explicit human review, FastAPI approval controls and an idempotent local ERP simulation. Demonstrated reviewed 3-line CSV export and no-duplicate handoff on one Colab sample; 41 deterministic offline tests passed in the repository baseline. Real-world extraction accuracy and live RPA deployment require further validation.
