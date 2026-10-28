from fastapi.testclient import TestClient

from invoiceflow_ai.backend_app import create_app


def test_placeholder_from_colab_review_rejected(tmp_path):
    client = TestClient(create_app(db_path=str(tmp_path / "db.sqlite3"), api_key="test-key-with-strong-length"))
    headers = {"X-API-Key": "test-key-with-strong-length"}
    valid_row = {"stt": 1, "ten_dich_vu": "Synthetic Demo Medicine", "so_luong": 2,
                 "don_vi_tinh": "Viên", "don_gia": 2000, "thanh_tien": 4000}
    imported = client.post("/api/v1/documents/import", headers=headers,
                           json={"result": {"items": [valid_row], "document_type": "MEDICINE_INVOICE"}})
    assert imported.status_code == 201
    doc_id = imported.json()["id"]
    for placeholder in ("REVIEW", "APPROVE", "TEST", "PLACEHOLDER", "N/A"):
        response = client.put(f"/api/v1/documents/{doc_id}/review", headers=headers,
                              json={"reviewer_id": "operator", "reviewed_document_type": "MEDICINE_INVOICE",
                                    "confirm_names_and_values": True,
                                    "items": [{**valid_row, "ten_dich_vu": placeholder}]})
        assert response.status_code == 422
    assert client.get(f"/api/v1/documents/{doc_id}/export.csv", headers=headers).status_code == 409
    assert client.get(f"/api/v1/documents/{doc_id}", headers=headers).json()["status"] == "PENDING_REVIEW"
