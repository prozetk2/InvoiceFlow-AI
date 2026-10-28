# Repository consolidation — validation record

**Build:** InvoiceFlow AI package 0.6.0 (local demo)

## Executed checks in the artifact-generation environment

```text
python -m pytest -q                 -> 41 tests PASS
python -m compileall -q src tests scripts -> PASS
invoiceflow --help                  -> PASS
python scripts/demo_workflow.py     -> END-TO-END SYNTHETIC DEMO PASSED
```

The real default unit/integration suite uses deterministic fake OCR/LLM responses and local temporary SQLite databases; **no real PaddleOCR weights or Gemini API were used in this verification**. A separate real Colab prototype was tested interactively by the user earlier, but its images and raw sensitive-looking outputs are intentionally not included in this public repository.

## Additional test coverage added during consolidation

- Standalone payment baseline with synthetic BHYT/cost/patient settlement rows.
- Route selection and one actual OCR prediction across the integrated pipeline.
- UNKNOWN layout refuses external LLM unless explicitly enabled.
- Server-side rejection of mistaken operator command placeholders (`REVIEW`, `APPROVE`, etc.) as product names.
- Import, full human review/approval workflow, blocked pre-approval CSV, idempotent local ERP simulation.

## Not tested here / not implemented

- Actual PaddleOCR model initialization and optical recognition from new images on target operating systems.
- Real Gemini network calls or provider availability in this artifact environment.
- Streamlit runtime/UI browser automation (optional dependency not installed here).
- Docker build on remote infrastructure.
- Production security/PHI compliance, real Power Automate Desktop, UiPath or enterprise ERP.

See `README.md`, `docs/EVALUATION.md`, `docs/SECURITY.md` for evaluation methodology and constraints.

## README result cards update (2026-10-28)

Reran checks after adding `assets/results/*.png`, `docs/RESULTS.md`, and the reproducible image-rendering script:

```text
PYTHONPATH=src python -m pytest -q             -> 41 passed
PYTHONPATH=src python scripts/demo_workflow.py -> END-TO-END SYNTHETIC DEMO PASSED
python -m compileall -q src tests scripts       -> PASS
```

These run under a source-tree `PYTHONPATH` because the package is not installed into the artifact build environment. The normal README setup runs `pip install -e ".[dev]"` first. The evidence PNGs are **faithful data visualizations reconstructed from previously shared Colab output**, not real screenshots or model-wide accuracy measurements.

## Stability fixes, package 0.7.0 (2026-10-28)

Reran after the review/approval, validation and extraction fixes (Python 3.13.17, FastAPI 0.162.0, Pydantic 2.14.1, NumPy 2.5.4, installed with `pip install -e ".[dev]"`):

```text
python -m pytest -q                              -> 53 passed
python -m compileall -q src tests scripts frontend -> PASS
python scripts/demo_workflow.py                   -> END-TO-END SYNTHETIC DEMO PASSED
```

The three new extraction regression tests (row alignment, strict number parsing, payment header aliases) were also run against the previous source and fail there, as intended. The Streamlit review screen was smoke-tested with `streamlit.testing.v1.AppTest` (Streamlit 1.66.1) against a live local API: zero-row document opens for manual entry, self-approval shows HTTP 403, a second person's approval with the approver key succeeds. Still not tested: real PaddleOCR, Gemini, Docker build.
