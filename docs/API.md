# FastAPI API reference — version 0.5.0 baseline

Default server binds `127.0.0.1:8000`. `X-API-Key: <INVOICEFLOW_API_KEY>` required for all `/api/v1/` document routes; `/health` is public. Full generated docs at `/docs`.

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/health` | Basic liveness |
| POST | `/api/v1/documents/import` | Import structured AI result (`source`, `result`) |
| POST | `/api/v1/documents/upload` | Upload PNG/JPEG/WEBP; **503 unless local OCR processor configured** |
| GET | `/api/v1/documents?limit=20` | Document metadata, limit 1..100 |
| GET | `/api/v1/documents/{id}` | Raw result, reviewed items, current status |
| GET | `/api/v1/documents/{id}/audit` | Audit actions (not raw payloads) |
| PUT | `/api/v1/documents/{id}/review` | Correct all rows and explicitly confirm values |
| POST | `/api/v1/documents/{id}/approve` | Approve a saved REVIEWED record |
| GET | `/api/v1/documents/{id}/export.csv` | CSV only if APPROVED; else HTTP 409 |

## Import

```json
{
  "source": "local_synthetic_demo",
  "result": {
    "status": "EXTRACTED",
    "document_type": "MEDICINE_INVOICE",
    "qa_status": "PENDING_HUMAN_REVIEW",
    "ready_for_rpa": false,
    "items": [{"stt":1,"ten_dich_vu":"DEMO Vitamin","so_luong":2,"don_vi_tinh":"Viên","don_gia":1500,"thanh_tien":3000}]
  }
}
```

The server never treats `result.ready_for_rpa` from the model as authorization. Initial status is either `PENDING_REVIEW` or `BLOCKED_VALIDATION` based on QA and items.

## Human review

`PUT /api/v1/documents/{id}/review`:

```json
{
  "reviewer_id": "demo_operator",
  "reviewed_document_type": "MEDICINE_INVOICE",
  "confirm_names_and_values": true,
  "items": [{"stt":1,"ten_dich_vu":"DEMO Vitamin","so_luong":2,"don_vi_tinh":"Viên","don_gia":1500,"thanh_tien":3000,"payment_details":null}]
}
```

All rows must be included; no silent omission. For `MEDICAL_PAYMENT`, each item must contain `payment_details` with `chi_phi`, `bhyt_chi`, `so_giam`, `benh_nhan_thanh_toan` that balance. The `reviewed_document_type` may be manually confirmed even if the original router returned `UNKNOWN`. Successful review returns `REVIEWED`, not yet export-eligible.

## Approval

`POST /api/v1/documents/{id}/approve`:

```json
{"reviewer_id":"demo_operator","acknowledge_review":true}
```

Must match stored reviewer ID; `status=REVIEWED`; backend re-validates persisted reviewed data and writes an approval audit event.

## Export

`GET /api/v1/documents/{id}/export.csv` requires `APPROVED`; outputs UTF-8 BOM CSV with columns `stt,ten_dich_vu,so_luong,don_vi_tinh,don_gia,thanh_tien`. Reject data injection attempts in spreadsheets. Returns 409 when unapproved.

## Common error codes

| HTTP | Meaning |
| --- | --- |
| 401 | API key invalid/missing |
| 404 | Document ID not found |
| 409 | Invalid workflow state / export before approval |
| 413 | Too-large image or extraction result |
| 415 | Unsupported uploaded MIME type |
| 422 | Invalid schema, values or arithmetic |
| 503 | OCR processor not configured |

## Serving safety

This API is intended for `localhost` development, not public patient data. Access control is a single demo API key, not OIDC/RBAC. The service does not encrypt SQLite contents at rest. Do not deploy on a public host without security engineering and a threat model.
