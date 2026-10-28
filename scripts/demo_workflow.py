"""Deterministic synthetic end-to-end demo; no OCR model, Gemini, or real ERP.

Commands:
    python scripts/demo_workflow.py
Uses temporary SQLite files, API TestClient and the approved CSV handoff.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from invoiceflow_ai.backend_app import create_app
from invoiceflow_ai.rpa_handoff import (
    get_demo_erp_rows, get_handoff_events, run_demo_worker, submit_approved_job,
)


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="invoiceflow_demo_") as folder:
        app = create_app(db_path=str(Path(folder) / "review.sqlite3"), api_key="local-demo-key-not-for-public-use")
        with TestClient(app) as client:
            headers = {"X-API-Key": "local-demo-key-not-for-public-use"}
            rows = [
                {"stt": 1, "ten_dich_vu": "Demo vitamin 100mg", "so_luong": 2,
                 "don_vi_tinh": "Viên", "don_gia": 1500, "thanh_tien": 3000},
                {"stt": 2, "ten_dich_vu": "Demo saline 10ml", "so_luong": 1,
                 "don_vi_tinh": "Chai", "don_gia": 12000, "thanh_tien": 12000},
            ]
            imported = client.post("/api/v1/documents/import", headers=headers,
                                   json={"source": "synthetic_demo", "result": {
                                       "status": "EXTRACTED", "document_type": "MEDICINE_INVOICE",
                                       "qa_status": "PENDING_HUMAN_REVIEW", "items": rows,
                                       "ready_for_rpa": False,
                                   }})
            imported.raise_for_status()
            doc_id = imported.json()["id"]
            assert client.get(f"/api/v1/documents/{doc_id}/export.csv", headers=headers).status_code == 409
            reviewed = client.put(f"/api/v1/documents/{doc_id}/review", headers=headers,
                                  json={"reviewer_id": "synthetic_review", "reviewed_document_type": "MEDICINE_INVOICE",
                                        "confirm_names_and_values": True, "items": rows})
            reviewed.raise_for_status()
            assert reviewed.json()["status"] == "REVIEWED"
            approved = client.post(f"/api/v1/documents/{doc_id}/approve", headers=headers,
                                   json={"approver_id": "synthetic_approver", "acknowledge_review": True})
            approved.raise_for_status()
            assert approved.json()["status"] == "APPROVED"
            db_path = str(Path(folder) / "rpa.sqlite3")
            print("Approved:", doc_id)
            print("Handoff:", submit_approved_job(client=client, api_headers=headers,
                                                   document_id=doc_id, db_path=db_path))
            print("ERP demo:", run_demo_worker(document_id=doc_id, db_path=db_path))
            print("Duplicate run:", run_demo_worker(document_id=doc_id, db_path=db_path))
            assert len(get_demo_erp_rows(document_id=doc_id, db_path=db_path)) == 2
            print("Audit:", get_handoff_events(document_id=doc_id, db_path=db_path))
            print("END-TO-END SYNTHETIC DEMO PASSED — no real external operations")


if __name__ == "__main__":
    run()
