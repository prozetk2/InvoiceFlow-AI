# Architecture and design decisions

## Context

InvoiceFlow AI is a local portfolio demonstration of document ingestion, OCR/KIE, careful review, and structured export. It is not an EHR, electronic prescribing or clinical decision system.

## Major components

| Layer | Entry point | Dependency | Side effect |
| --- | --- | --- | --- |
| CLI | `invoiceflow_ai.cli:main` | Pillow/PaddleOCR if extract | Can save JSON locally |
| OCR | `PaddleOCREngine.predict` | Optional PaddleOCR 3.x | Model download/inference |
| Integration | `pipeline.process_invoice` | `document_router` & extractors | CPU inference / optional LLM |
| Routing | `classify_document` | Recognized headers | None |
| Medicine KIE | `process_medicine_final` | OCR cache and header rules | None |
| BHYT KIE | `process_medical_payment` | OCR cache | None |
| Unknown fallback | `llm_fallback_from_ocr` | Optional caller-supplied LLM | Network only with explicit opt-in |
| REST backend | `create_app` | FastAPI + SQLite | Stores reviewed/extracted text |
| UI | Streamlit client | FastAPI API key | Operator corrections |
| RPA handoff | `submit_approved_job` | Approved CSV + SQLite | Local handoff DB |
| Demo worker | `run_demo_worker` | SQLite | Local ERP simulator only |

## Decisions

**ADR-001 — One OCR per document.** Routing inspects one PaddleOCR result, then passes a `CachedOCREngine` to the chosen extractor. This guards latency and ensures box IDs are aligned across modules. The cache compares pixel buffers to prevent cross-document reuse.

**ADR-002 — Conservative routing.** Recognizing `STT/Tên thuốc/SL/ĐVT/Đơn giá/Số tiền` differs from `Chi phí/BHYT/BN`. A missing/conflicting signature yields UNKNOWN, which is acceptable abstention.

**ADR-003 — Evidence first.** The fallback LLM cites OCR boxes, and validators reject ungrounded amounts and missing STTs instead of hallucinating plausible values.

**ADR-004 — Dataset vs payment semantics.** `thanh_tien` is gross service cost for labeled payment rows; `benh_nhan_thanh_toan` is a separate payment field.

**ADR-005 — Human review is required.** Arithmetic matching and confidence do not verify OCR names. A separate review action and approval action are required before CSV export.

**ADR-006 — Local handoff before real RPA.** SQLite transactionality/idempotency allow safe demonstration and fault injection without interacting with a live ERP.

## Known limitations

- Header aliases are hand-designed for the supported layouts, not a general classifier.
- One-row-per-financial-cell alignment is a baseline; merged cells or complicated tables may fail.
- Product names can contain OCR typos despite high confidence; current UI requires full operator verification.
- No asynchronous processing queue, retry scheduler or production monitoring.
- Local SQLite has no encryption-at-rest in this demo.
- Shared API key and same reviewer/approver identity is not production-grade segregation of duties.
- Backend upload keeps external fallback disabled, even when Gemini is configured for CLI.
- None of the current integrations operates a real ERP or remote RPA desktop.

## Trust boundaries

1. Uploaded image / OCR text is untrusted.
2. LLM model output is untrusted.
3. Approved review record in the backend is the **authoritative** data for CSV.
4. The local handoff validates the approved CSV and content hash again.
5. A future remote RPA adapter must re-authenticate and enforce transaction-safe/idempotent processing.
