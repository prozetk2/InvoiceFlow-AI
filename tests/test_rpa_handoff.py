"""Module 05B safety/integration tests with synthetic data; no live RPA."""
import csv
import io

import pytest
from fastapi.testclient import TestClient
from invoiceflow_ai.backend_app import create_app
from invoiceflow_ai.rpa_handoff import (
    HandoffError, parse_approved_csv, submit_approved_job,
    run_demo_worker, get_demo_erp_rows, get_handoff_events,
)

HEADERS = {"x-api-key": "demo-api-key-with-enough-entropy"}

@pytest.fixture
def env(tmp_path):
    c = TestClient(create_app(db_path=str(tmp_path / "documents.sqlite3"), api_key=HEADERS["x-api-key"]))
    db = str(tmp_path / "demo_erp.sqlite3")
    return c, db


def make_doc(c, *, approve=True):
    doc = c.post("/api/v1/documents/import", headers=HEADERS, json={
        "source": "synthetic_test",
        "result": {"document_type": "UNKNOWN", "status": "FALLBACK_EXTRACTED",
                   "qa_status": "PENDING_HUMAN_REVIEW", "items": [{"stt": 1, "ten_dich_vu": "Synthetic pill",
                     "so_luong": 2, "don_vi_tinh": "Viên", "don_gia": 1000, "thanh_tien": 2000}]}
    }).json()
    doc_id = doc["id"]
    if approve:
        response = c.put(f"/api/v1/documents/{doc_id}/review", headers=HEADERS,
                         json={"reviewer_id": "tester", "reviewed_document_type": "MEDICINE_INVOICE",
                               "confirm_names_and_values": True,
                               "items": [{"stt": 1, "ten_dich_vu": "Synthetic pill", "so_luong": 2,
                                          "don_vi_tinh": "Viên", "don_gia": 1000, "thanh_tien": 2000}]})
        assert response.status_code == 200
        response = c.post(f"/api/v1/documents/{doc_id}/approve", headers=HEADERS,
                          json={"approver_id": "second_person", "acknowledge_review": True})
        assert response.status_code == 200
    return doc_id


def make_csv(name="Demo pill", total="2000"):
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien"])
    w.writerow([1, name, 2, "Viên", 1000, total])
    return out.getvalue().encode("utf-8-sig")


def test_approved_handoff_and_worker_idempotent(env):
    c, db = env
    id_ = make_doc(c)
    result = submit_approved_job(client=c, api_headers=HEADERS, document_id=id_, db_path=db)
    assert result["state"] == "PENDING" and result["action"] == "CREATED" and result["row_count"] == 1
    assert submit_approved_job(client=c, api_headers=HEADERS, document_id=id_, db_path=db)["action"] == "ALREADY_SUBMITTED"
    assert run_demo_worker(document_id=id_, db_path=db)["action"] == "SIMULATED_NOW"
    assert run_demo_worker(document_id=id_, db_path=db)["action"] == "ALREADY_SIMULATED"
    rows = get_demo_erp_rows(document_id=id_, db_path=db)
    assert len(rows) == 1 and rows[0]["ten_dich_vu"] == "Synthetic pill"
    assert [e["event"] for e in get_handoff_events(document_id=id_, db_path=db)] == ["HANDOFF_ACCEPTED", "LOCAL_ERP_SIMULATED"]


def test_unapproved_document_blocked(env):
    c, db = env
    id_ = make_doc(c, approve=False)
    with pytest.raises(HandoffError, match="APPROVED"):
        submit_approved_job(client=c, api_headers=HEADERS, document_id=id_, db_path=db)
    assert get_demo_erp_rows(document_id=id_, db_path=db) == []


def test_bad_payloads():
    for kwargs in ({"name":"REVIEW"}, {"name":"Approved."}, {"name":"'=SUM(1+1)"}, {"name":"-gel"},
                   {"total":"999"}, {"total":"\u0662\u0660\u0660\u0660"}):
        with pytest.raises(HandoffError):
            parse_approved_csv(make_csv(**kwargs))
    assert parse_approved_csv(make_csv())[0]["thanh_tien"] == 2000


def test_duplicate_stt_rejected():
    text = make_csv().decode("utf-8-sig")
    lines = text.splitlines()
    with pytest.raises(HandoffError, match="duplicate"):
        parse_approved_csv(("\n".join(lines + [lines[-1]])).encode("utf-8-sig"))


def test_csv_not_matching_approved_hash_rejected(env):
    c, db = env
    id_ = make_doc(c)

    class TamperingClient:
        """Returns a CSV that differs from what the backend approved."""
        def get(self, url, headers):
            response = c.get(url, headers=headers)
            if url.endswith("export.csv"):
                response._content = make_csv(name="Synthetic pill", total="2000").replace(b"Synthetic pill", b"Other pill")
            return response

    with pytest.raises(HandoffError, match="approved payload hash"):
        submit_approved_job(client=TamperingClient(), api_headers=HEADERS, document_id=id_, db_path=db)
    assert get_demo_erp_rows(document_id=id_, db_path=db) == []


def test_unknown_job_cannot_execute(env):
    _, db = env
    with pytest.raises(HandoffError, match="not found"):
        run_demo_worker(document_id="0"*32, db_path=db)
