"""Module 05A: local API workflow safety/regression tests with synthetic records."""
import csv
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from invoiceflow_ai.backend_app import create_app

API_KEY = "local-test-token-very-secret"
HEADERS = {"x-api-key": API_KEY}

@pytest.fixture()
def setup(tmp_path):
    app = create_app(db_path=str(tmp_path / "invoiceflow.sqlite3"), api_key=API_KEY)
    return app, TestClient(app)


def sample_result(*, qa="PENDING_HUMAN_REVIEW", name="Demo pill 20mg"):
    return {
        "status": "FALLBACK_EXTRACTED",
        "qa_status": qa,
        "document_type": "UNKNOWN",
        "ready_for_rpa": True,  # malicious/external claim must NOT grant approval
        "items": [{
            "stt": 1, "ten_dich_vu": name, "so_luong": 2,
            "don_vi_tinh": "Viên", "don_gia": 1500, "thanh_tien": 3000,
            "warnings": [],
        }],
        "ocr_inference_count": 1,
    }


def review_payload(*, name="Demo pill 20mg", price=1500, total=3000):
    return {"reviewer_id": "reviewer_demo", "reviewed_document_type": "MEDICINE_INVOICE",
            "confirm_names_and_values": True,
            "items": [{"stt": 1, "ten_dich_vu": name, "so_luong": 2,
                       "don_vi_tinh": "Viên", "don_gia": price, "thanh_tien": total}]}


def import_one(client, result=None):
    res = client.post("/api/v1/documents/import", headers=HEADERS,
                      json={"result": result if result is not None else sample_result()})
    assert res.status_code == 201, res.text
    return res.json()


def test_authentication_and_health(setup):
    _, c = setup
    assert c.get("/health").status_code == 200
    assert c.get("/api/v1/documents").status_code == 401
    assert c.get("/api/v1/documents", headers={"x-api-key": "invalid"}).status_code == 401
    assert c.get("/api/v1/documents", headers=HEADERS).status_code == 200


def test_cannot_approve_unreviewed_or_export(setup):
    _, c = setup
    doc = import_one(c)
    assert doc["ready_for_rpa"] is False
    assert c.get(f"/api/v1/documents/{doc['id']}/export.csv", headers=HEADERS).status_code == 409
    assert c.post(f"/api/v1/documents/{doc['id']}/approve", headers=HEADERS,
                  json={"reviewer_id": "reviewer_demo", "acknowledge_review": True}).status_code == 409


def test_review_approval_export_audit(setup):
    _, c = setup
    doc = import_one(c, sample_result(name="'=SUM(1+1)"))
    docid = doc["id"]
    reviewed = c.put(f"/api/v1/documents/{docid}/review", headers=HEADERS,
                     json=review_payload(name="=SUM(1+1)"))
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "REVIEWED"
    assert reviewed.json()["ready_for_rpa"] is False
    wrong = c.post(f"/api/v1/documents/{docid}/approve", headers=HEADERS,
                   json={"reviewer_id": "someone_else", "acknowledge_review": True})
    assert wrong.status_code == 403
    approved = c.post(f"/api/v1/documents/{docid}/approve", headers=HEADERS,
                      json={"reviewer_id": "reviewer_demo", "acknowledge_review": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["ready_for_rpa"] is True
    csv_response = c.get(f"/api/v1/documents/{docid}/export.csv", headers=HEADERS)
    assert csv_response.status_code == 200, csv_response.text
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0]["ten_dich_vu"] == "'=SUM(1+1)"
    assert rows[0]["thanh_tien"] == "3000"
    assert c.put(f"/api/v1/documents/{docid}/review", headers=HEADERS,
                 json=review_payload()).status_code == 409
    actions = [a["action"] for a in c.get(f"/api/v1/documents/{docid}/audit", headers=HEADERS).json()]
    assert actions == ["IMPORTED", "REVIEW_SUBMITTED", "APPROVED", "CSV_EXPORTED"]


def test_wrong_arithmetic_cannot_review(setup):
    _, c = setup
    doc = import_one(c)
    response = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                     json=review_payload(price=1500, total=9999))
    assert response.status_code == 422
    assert c.get(f"/api/v1/documents/{doc['id']}", headers=HEADERS).json()["status"] == "PENDING_REVIEW"


def test_blocked_extraction_can_be_manually_corrected(setup):
    _, c = setup
    doc = import_one(c, sample_result(qa="BLOCKED_VALIDATION_ERRORS"))
    assert doc["status"] == "BLOCKED_VALIDATION"
    res = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                json=review_payload())
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "REVIEWED"


def test_unknown_requires_document_type_confirmation(setup):
    _, c = setup
    doc = import_one(c)
    payload = review_payload()
    payload["reviewed_document_type"] = "UNKNOWN"
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                 json=payload).status_code == 422


def test_payment_details_required_and_balanced(setup):
    _, c = setup
    doc = import_one(c)
    payload = review_payload()
    payload["reviewed_document_type"] = "MEDICAL_PAYMENT"
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                 json=payload).status_code == 422
    payload["items"][0]["payment_details"] = {
        "chi_phi": 3000, "bhyt_chi": 1000, "so_giam": 0, "benh_nhan_thanh_toan": 2000
    }
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                 json=payload).status_code == 200


def test_review_wont_silently_drop_extracted_rows(setup):
    _, c = setup
    orig = sample_result()
    orig["items"].append({**orig["items"][0], "stt":2})
    doc = import_one(c, orig)
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                 json=review_payload()).status_code == 422


def test_upload_processor_unconfigured_and_mocked(setup):
    app, c = setup
    img = Image.new("RGB", (5, 5), "white")
    stream = io.BytesIO()
    img.save(stream, format="PNG")
    def send_image():
        return c.post("/api/v1/documents/upload", headers=HEADERS,
                      files={"file": ("synthetic.png", stream.getvalue(), "image/png")})
    assert send_image().status_code == 503
    app.state.document_processor = lambda _: sample_result()
    uploaded = send_image()
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["ready_for_rpa"] is False
    assert uploaded.json()["raw_result"]["ocr_inference_count"] == 1


def test_no_approval_when_not_acknowledged(setup):
    _, c = setup
    doc = import_one(c)
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS,
                 json=review_payload()).status_code == 200
    assert c.post(f"/api/v1/documents/{doc['id']}/approve", headers=HEADERS,
                  json={"reviewer_id": "reviewer_demo", "acknowledge_review": False}).status_code == 422
    assert c.get(f"/api/v1/documents/{doc['id']}", headers=HEADERS).json()["ready_for_rpa"] is False
