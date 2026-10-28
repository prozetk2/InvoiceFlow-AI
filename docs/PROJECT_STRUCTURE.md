# Module map and extension points

Use `README.md` for commands and `AGENTS.md` for development procedure.

## Public interfaces

```python
from invoiceflow_ai.pipeline import process_invoice
from invoiceflow_ai.backend_app import create_app
from invoiceflow_ai.rpa_handoff import submit_approved_job, run_demo_worker
```

### Extraction result

- `document_type`: `MEDICINE_INVOICE`, `MEDICAL_PAYMENT`, or `UNKNOWN`.
- `status`: `EXTRACTED`, `EXTRACTED_PAYMENT`, `FALLBACK_EXTRACTED`, `FALLBACK_PARTIAL`, ...
- `qa_status`: e.g., `PENDING_HUMAN_REVIEW`, `BLOCKED_VALIDATION_ERRORS`.
- `items`: six normalized fields plus field-specific metadata/evidence.
- `routing`: classifier evidence and reason.
- `ocr_inference_count=1` and `ocr_cache_hits=1` for known routed layout.
- `ready_for_rpa=False` — always until independent backend approval.

### Backend record

The backend stores `raw_result_json` and later `reviewed_items_json`, reviewed type, reviewer ID and an audit trail. SQLite tables are initialized automatically on `create_app`.

### RPA job

RPA tables are separate from review DB, and accepted jobs must be sourced from approved CSV. State transitions `PENDING → SIMULATED` are local-demo only.

## Extending document support

1. Add new family evidence to `classify_document`, preserving UNKNOWN handling.
2. Implement extractor with one cached OCR call, field provenance, warnings and normalization.
3. Add domain-specific arithmetic QA and human review semantics.
4. Update `pipeline.py` and backend type schema if a new reviewed family is needed.
5. Add synthetic fake OCR tests and test on **independent** de-identified real documents.
6. Update README/AGENTS/RULES and user-facing UI support.

## Version policy

Package version is `0.7.0`; the backend health endpoint reports API version `0.6.0` (approval now takes `approver_id`, review accepts `row_count_change_reason`, export adds `X-Payload-SHA256`). Treat API changes as separately versioned interface changes; do not assume semantic API stability from package number alone.
