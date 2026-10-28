# Migrating the original Google Colab prototype

The initial experiment used numbered Colab cells (17/20/.../51), OCR with `PaddleOCR(lang='vi', ocr_version='PP-OCRv6')`, dataset `minhduc168/dataset-origin-vlm-extract-bill`, and individual KIE/quality-gate modules. This repository replaces duplicated notebook functions with canonical, importable modules.

## Old → new mapping

| Earlier notebook | Current import |
| --- | --- |
| `detect_header`, `reconstruct_headers` | `invoiceflow_ai.header_detection` |
| `process_medicine_document_v3` + medicine QA | `invoiceflow_ai.medicine_table_extractor.process_medicine_final` |
| `extract_medical_payment_v2` + payment QA | `invoiceflow_ai.medical_payment_extractor.process_medical_payment` |
| `route_document` | `invoiceflow_ai.document_router.route_document` |
| `process_document_with_fallback` | `invoiceflow_ai.pipeline.process_invoice` |
| Gemini generate adapter | `invoiceflow_ai.gemini_adapter.create_gemini_generator` |
| FastAPI app | `invoiceflow_ai.backend_app.create_app` |
| Handoff worker | `invoiceflow_ai.rpa_handoff` |

## In Colab after cloning/installation

```python
!git clone https://github.com/YOUR_USERNAME/invoiceflow-ai.git
%cd invoiceflow-ai
!pip install -e .

from invoiceflow_ai.pipeline import process_invoice
# Reuse a real PaddleOCR engine initialized in your notebook:
# result = process_invoice(val_ds[2]['image'], ocr)
```

To enable the standalone OCR engine in Colab, install compatible PaddlePaddle/PaddleOCR runtime separately, then:

```python
from invoiceflow_ai.ocr_adapter import PaddleOCREngine
engine = PaddleOCREngine(device='cpu', lang='vi')
# result = process_invoice(deidentified_image, engine)
```

For unknown OCR layouts, `llm_generate` and `allow_external_processing=True` are only permitted after explicit data clearance. Example:

```python
from invoiceflow_ai.gemini_adapter import create_gemini_generator
llm_generate = create_gemini_generator()
# result = process_invoice(authorized_deidentified_image, engine,
#                          llm_generate=llm_generate, allow_external_processing=True)
```

The original stored `fallback_result` and `fixed_result` can still be imported into the backend using `POST /api/v1/documents/import`; the new package does not depend on global notebook variables.

## Important differences

- Public package includes a **new standalone BHYT extractor** and standalone generic header detector; they follow the same algorithm family but are not byte-for-byte copies of the Colab functions. Retest on your de-identified invoices before claiming result parity.
- Production-ready security, privacy, job queue and actual RPA are **not** included; see `docs/SECURITY.md`.
- The original real Colab dataset and scanned images are intentionally not in Git.
