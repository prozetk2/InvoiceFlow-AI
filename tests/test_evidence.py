"""Evidence viewer: stored source image, evidence boxes per field, overlay rendering."""
from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from test_standalone_pipeline import FakeOCR, payment_data

from invoiceflow_ai.backend_app import create_app
from invoiceflow_ai.evidence import build_evidence, render_overlay
from invoiceflow_ai.pipeline import process_invoice

API_KEY = "local-test-token-very-secret"
HEADERS = {"x-api-key": API_KEY}


def png(size=(700, 400), color="white") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def payment_result(size=(700, 400)):
    return process_invoice(Image.new("RGB", size), FakeOCR(payment_data()))


def client_for(tmp_path, *, processor=None, store_images=None):
    app = create_app(db_path=str(tmp_path / "db.sqlite3"), api_key=API_KEY, store_images=store_images)
    app.state.document_processor = processor
    return TestClient(app)


def upload(client, data=None):
    return client.post("/api/v1/documents/upload", headers=HEADERS,
                       files={"file": ("doc.png", data or png(), "image/png")})


def test_build_evidence_maps_fields_to_boxes():
    result = payment_result()
    evidence = build_evidence(result)
    assert evidence["image_size"] == [700, 400] and evidence["has_geometry"]
    first = evidence["rows"][0]
    assert first["stt"] == 1
    assert set(first["fields"]) >= {"stt", "ten_dich_vu", "chi_phi", "bhyt_chi", "benh_nhan_thanh_toan"}
    raw = payment_data()
    assert first["fields"]["chi_phi"] == [raw["rec_boxes"][raw["rec_texts"].index("100,000")]]


def test_build_evidence_ignores_bad_ids_and_old_results():
    result = {"ocr_boxes": [[1, 2, 3, 4], None],
              "items": [{"stt": 1, "evidence_box_ids": {"stt": [0, 1, 7, "x", True], "so_luong": 5}},
                        {"stt": 2}]}
    rows = build_evidence(result)["rows"]
    assert rows[0]["fields"] == {"stt": [[1, 2, 3, 4]]}
    assert rows[1]["fields"] == {}
    assert build_evidence({"items": [{"stt": 1}]})["has_geometry"] is False


def test_render_overlay_draws_selected_row():
    evidence = build_evidence(payment_result())
    plain = Image.open(io.BytesIO(render_overlay(png(), evidence, row_index=99)))
    drawn = Image.open(io.BytesIO(render_overlay(png(), evidence, row_index=0)))
    assert plain.size == drawn.size == (700, 400)
    assert plain.getcolors() == [(700 * 400, (255, 255, 255))]  # nothing to draw for a missing row
    assert len(drawn.getcolors(maxcolors=10_000)) > 1


def test_upload_stores_image_and_serves_evidence(tmp_path):
    client = client_for(tmp_path, processor=lambda image: payment_result(image.size))
    data = png()
    doc = upload(client, data).json()
    assert doc["has_image"] is True
    image = client.get(f"/api/v1/documents/{doc['id']}/image", headers=HEADERS)
    assert image.status_code == 200 and image.content == data
    assert image.headers["cache-control"] == "no-store"
    evidence = client.get(f"/api/v1/documents/{doc['id']}/evidence", headers=HEADERS).json()
    assert evidence["has_image"] and len(evidence["rows"]) == 3
    audit = client.get(f"/api/v1/documents/{doc['id']}/audit", headers=HEADERS).json()
    stored = [a for a in audit if a["action"] == "IMAGE_STORED"]
    assert stored and stored[0]["details"]["width"] == 700 and "data" not in stored[0]["details"]


def test_image_storage_can_be_disabled(tmp_path):
    client = client_for(tmp_path, processor=lambda image: payment_result(image.size), store_images=False)
    doc = upload(client).json()
    assert doc["has_image"] is False
    assert client.get(f"/api/v1/documents/{doc['id']}/image", headers=HEADERS).status_code == 404
    response = client.put(f"/api/v1/documents/{doc['id']}/image", headers=HEADERS,
                          files={"file": ("doc.png", png(), "image/png")})
    assert response.status_code == 403


def import_payment(client):
    response = client.post("/api/v1/documents/import", headers=HEADERS,
                           json={"result": payment_result(), "source": "colab"})
    assert response.status_code == 201
    return response.json()


def test_attach_image_to_imported_result_once(tmp_path):
    client = client_for(tmp_path)
    doc = import_payment(client)
    assert doc["has_image"] is False
    url = f"/api/v1/documents/{doc['id']}/image"
    attached = client.put(url, headers=HEADERS, files={"file": ("doc.png", png(), "image/png")})
    assert attached.status_code == 201 and attached.json()["has_image"] is True
    again = client.put(url, headers=HEADERS, files={"file": ("doc.png", png(), "image/png")})
    assert again.status_code == 409


@pytest.mark.parametrize("size", [(701, 400), (400, 700)])
def test_attach_rejects_image_of_another_size(tmp_path, size):
    client = client_for(tmp_path)
    doc = import_payment(client)
    response = client.put(f"/api/v1/documents/{doc['id']}/image", headers=HEADERS,
                          files={"file": ("doc.png", png(size), "image/png")})
    assert response.status_code == 422


def test_attach_rejects_non_images_and_missing_key(tmp_path):
    client = client_for(tmp_path)
    doc = import_payment(client)
    url = f"/api/v1/documents/{doc['id']}/image"
    assert client.put(url, headers=HEADERS,
                      files={"file": ("doc.txt", b"hello", "text/plain")}).status_code == 415
    assert client.put(url, files={"file": ("doc.png", png(), "image/png")}).status_code == 401
    assert client.get(url).status_code == 401
