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
                  json={"approver_id": "approver_demo", "acknowledge_review": True}).status_code == 409


def test_review_approval_export_audit(setup):
    _, c = setup
    doc = import_one(c, sample_result(name="Demo pill 2Omg"))
    docid = doc["id"]
    reviewed = c.put(f"/api/v1/documents/{docid}/review", headers=HEADERS,
                     json=review_payload(name="Demo pill 20mg"))
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["status"] == "REVIEWED"
    assert reviewed.json()["ready_for_rpa"] is False
    # Four-eyes: the reviewer cannot approve their own correction.
    for same in ("reviewer_demo", " Reviewer_Demo "):
        own = c.post(f"/api/v1/documents/{docid}/approve", headers=HEADERS,
                     json={"approver_id": same, "acknowledge_review": True})
        assert own.status_code == 403
    approved = c.post(f"/api/v1/documents/{docid}/approve", headers=HEADERS,
                      json={"approver_id": "approver_demo", "acknowledge_review": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["ready_for_rpa"] is True
    assert approved.json()["approver_id"] == "approver_demo"
    digest = approved.json()["approved_payload_sha256"]
    assert len(digest) == 64
    csv_response = c.get(f"/api/v1/documents/{docid}/export.csv", headers=HEADERS)
    assert csv_response.status_code == 200, csv_response.text
    assert csv_response.headers["X-Payload-SHA256"] == digest
    rows = list(csv.DictReader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0]["ten_dich_vu"] == "Demo pill 20mg"
    assert rows[0]["thanh_tien"] == "3000"
    assert c.put(f"/api/v1/documents/{docid}/review", headers=HEADERS,
                 json=review_payload()).status_code == 409
    actions = [a["action"] for a in c.get(f"/api/v1/documents/{docid}/audit", headers=HEADERS).json()]
    assert actions == ["IMPORTED", "REVIEW_SUBMITTED", "APPROVED", "CSV_EXPORTED"]
    review_event = c.get(f"/api/v1/documents/{docid}/audit", headers=HEADERS).json()[1]
    # Audit names which fields changed, without copying the values (R-303).
    assert review_event["details"]["changed_rows"] == [{"stt": 1, "fields": ["ten_dich_vu"]}]
    assert "Demo pill" not in str(review_event["details"])


def test_formula_like_text_rejected_at_review(setup):
    _, c = setup
    doc = import_one(c)
    for name in ("=SUM(1+1)", "'=SUM(1+1)", "-Demo gel", "@cmd", "+1 tablet"):
        res = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=review_payload(name=name))
        assert res.status_code == 422, name
    payload = review_payload()
    payload["items"][0]["don_vi_tinh"] = "=HYPERLINK()"
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=payload).status_code == 422


def test_reviewer_can_add_missed_row_with_reason(setup):
    _, c = setup
    doc = import_one(c)
    payload = review_payload()
    payload["items"].append({"stt": 2, "ten_dich_vu": "Demo syrup 60ml", "so_luong": 1,
                             "don_vi_tinh": "Chai", "don_gia": 20000, "thanh_tien": 20000})
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=payload).status_code == 422
    payload["row_count_change_reason"] = "OCR missed row 2 on the source image"
    res = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=payload)
    assert res.status_code == 200, res.text
    event = c.get(f"/api/v1/documents/{doc['id']}/audit", headers=HEADERS).json()[-1]
    assert event["details"]["added_stts"] == [2]
    assert event["details"]["row_count_change_reason"] == "OCR missed row 2 on the source image"


def test_document_with_no_extracted_rows_can_be_entered_manually(setup):
    _, c = setup
    doc = import_one(c, {"status": "UNSUPPORTED_LAYOUT", "qa_status": "BLOCKED_NO_ITEMS", "items": []})
    assert doc["status"] == "BLOCKED_VALIDATION"
    payload = review_payload()
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=payload).status_code == 422
    payload["row_count_change_reason"] = "Extractor found no rows; entered from source image"
    res = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=payload)
    assert res.status_code == 200, res.text
    assert res.json()["status"] == "REVIEWED"


def test_re_review_clears_previous_approval_fields(setup):
    _, c = setup
    doc = import_one(c)
    first = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=review_payload())
    second = c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=review_payload())
    assert first.json()["review_version"] == 1 and second.json()["review_version"] == 2
    assert second.json()["approved_payload_sha256"] is None


def test_export_refuses_data_changed_after_approval(setup):
    import sqlite3
    app, c = setup
    doc = import_one(c)
    c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=review_payload())
    assert c.post(f"/api/v1/documents/{doc['id']}/approve", headers=HEADERS,
                  json={"approver_id": "approver_demo", "acknowledge_review": True}).status_code == 200
    con = sqlite3.connect(app.state.db_path)
    with con:
        con.execute("UPDATE documents SET reviewed_items_json=replace(reviewed_items_json, '3000', '3001')")
    con.close()
    assert c.get(f"/api/v1/documents/{doc['id']}/export.csv", headers=HEADERS).status_code == 409


def test_separate_approver_key(tmp_path):
    approver_key = "approver-key-very-secret"
    c = TestClient(create_app(db_path=str(tmp_path / "db.sqlite3"), api_key=API_KEY,
                              approver_api_key=approver_key))
    approver_headers = {"x-api-key": approver_key}
    doc = import_one(c)
    assert c.post("/api/v1/documents/import", headers=approver_headers,
                  json={"result": sample_result()}).status_code == 403
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=approver_headers,
                 json=review_payload()).status_code == 403
    assert c.put(f"/api/v1/documents/{doc['id']}/review", headers=HEADERS, json=review_payload()).status_code == 200
    body = {"approver_id": "approver_demo", "acknowledge_review": True}
    assert c.post(f"/api/v1/documents/{doc['id']}/approve", headers=HEADERS, json=body).status_code == 403
    assert c.post(f"/api/v1/documents/{doc['id']}/approve", headers=approver_headers, json=body).status_code == 200
    with pytest.raises(RuntimeError):
        create_app(db_path=str(tmp_path / "db2.sqlite3"), api_key=API_KEY, approver_api_key=API_KEY)


def test_older_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE documents (id TEXT PRIMARY KEY, status TEXT NOT NULL,
        original_document_type TEXT NOT NULL, reviewed_document_type TEXT, raw_result_json TEXT NOT NULL,
        reviewed_items_json TEXT, reviewer_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    con.commit()
    con.close()
    c = TestClient(create_app(db_path=str(path), api_key=API_KEY))
    doc = import_one(c)
    assert doc["review_version"] == 0 and doc["approver_id"] is None


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
                  json={"approver_id": "approver_demo", "acknowledge_review": False}).status_code == 422
    assert c.get(f"/api/v1/documents/{doc['id']}", headers=HEADERS).json()["ready_for_rpa"] is False
