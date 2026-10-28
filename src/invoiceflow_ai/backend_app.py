"""InvoiceFlow AI Module 05A — local-demo FastAPI review & export backend.

This service consumes the output of Modules 01–04, stores it in SQLite,
requires a separate authenticated human-review action, validates corrections,
then requires explicit approval before CSV export for an RPA operator.

NOT a clinical record system or production PHI service. No background jobs,
LLM requests, or live RPA execution are performed here.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .evidence import build_evidence
from .validation import EXPORT_COLUMNS, is_formula_like, is_placeholder_name, payload_sha256

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 200
MAX_IMAGE_PIXELS = 16_000_000
DEFAULT_DB_PATH = "data/invoiceflow.sqlite3"
MIN_REASON_LENGTH = 10
IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_json(value: Any) -> str:
    """JSON serialize numpy numeric types if present; reject non-finite floats."""
    def default(obj: Any) -> Any:
        if hasattr(obj, "item") and callable(obj.item):
            return obj.item()
        raise TypeError(f"Unsupported JSON type: {type(obj).__name__}")
    return json.dumps(value, ensure_ascii=False, default=default, allow_nan=False)


def _load_json(value: str | None) -> Any:
    return json.loads(value) if value else None


@contextmanager
def _conn(db_path: str) -> Iterator[sqlite3.Connection]:
    """One transaction per block: commit on success, roll back on error, always close."""
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


# Columns added in package 0.7.0; applied to older databases on startup.
_DOCUMENT_MIGRATIONS = {
    "approver_id": "TEXT",
    "approved_payload_sha256": "TEXT",
    "review_version": "INTEGER NOT NULL DEFAULT 0",
}


def _init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _conn(db_path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            original_document_type TEXT NOT NULL,
            reviewed_document_type TEXT,
            raw_result_json TEXT NOT NULL,
            reviewed_items_json TEXT,
            reviewer_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL REFERENCES documents(id),
            action TEXT NOT NULL,
            actor TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")
        # Source image kept as review evidence: the exact bytes OCR saw, so
        # evidence box coordinates line up. Same retention as the OCR text.
        con.execute("""CREATE TABLE IF NOT EXISTS document_images (
            document_id TEXT PRIMARY KEY REFERENCES documents(id),
            content_type TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            data BLOB NOT NULL,
            created_at TEXT NOT NULL
        )""")
        existing = {r["name"] for r in con.execute("PRAGMA table_info(documents)")}
        for column, ddl in _DOCUMENT_MIGRATIONS.items():
            if column not in existing:
                con.execute(f"ALTER TABLE documents ADD COLUMN {column} {ddl}")


def _record(con: sqlite3.Connection, doc_id: str, action: str,
            actor: str, details: dict | None = None) -> None:
    con.execute(
        "INSERT INTO audit_events(document_id, action, actor, details_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (doc_id, action, actor, _as_json(details or {}), _now()),
    )


def _get(con: sqlite3.Connection, document_id: str) -> sqlite3.Row:
    row = con.execute(
        "SELECT d.*, EXISTS(SELECT 1 FROM document_images i WHERE i.document_id=d.id) AS has_image "
        "FROM documents d WHERE d.id=?", (document_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Document not found")
    return row


def _public(row: sqlite3.Row) -> dict:
    status = row["status"]
    return {
        "id": row["id"],
        "status": status,
        "original_document_type": row["original_document_type"],
        "reviewed_document_type": row["reviewed_document_type"],
        "ready_for_rpa": status == "APPROVED",
        "raw_result": _load_json(row["raw_result_json"]),
        "reviewed_items": _load_json(row["reviewed_items_json"]),
        "reviewer_id": row["reviewer_id"],
        "approver_id": row["approver_id"],
        "approved_payload_sha256": row["approved_payload_sha256"],
        "review_version": row["review_version"],
        "has_image": bool(row["has_image"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


class ImportPayload(BaseModel):
    result: dict[str, Any]
    source: str = Field(default="colab", max_length=80)

    @field_validator("result")
    @classmethod
    def check_result(cls, value: dict) -> dict:
        if not isinstance(value.get("items"), list):
            raise ValueError("result.items must be a list")
        if len(value["items"]) > MAX_ITEMS:
            raise ValueError("Too many rows")
        for item in value["items"]:
            if not isinstance(item, dict):
                raise ValueError("result.items must contain objects")
        return value


class PaymentDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    chi_phi: int = Field(ge=0)
    bhyt_chi: int = Field(ge=0)
    so_giam: int = Field(ge=0)
    benh_nhan_thanh_toan: int = Field(ge=0)

    @model_validator(mode="after")
    def check_formula(self):
        if self.chi_phi - self.bhyt_chi - self.so_giam != self.benh_nhan_thanh_toan:
            raise ValueError("Payment formula does not balance")
        return self


class ReviewedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stt: int = Field(gt=0)
    ten_dich_vu: str = Field(min_length=1, max_length=500)
    so_luong: int = Field(gt=0)
    don_vi_tinh: str = Field(min_length=1, max_length=40)
    don_gia: int = Field(ge=0)
    thanh_tien: int = Field(ge=0)
    payment_details: PaymentDetails | None = None

    @field_validator("ten_dich_vu", "don_vi_tinh")
    @classmethod
    def nonblank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("Field cannot be blank")
        return clean

    @field_validator("ten_dich_vu")
    @classmethod
    def reject_unsafe_names(cls, value: str) -> str:
        # A literal operator command accidentally became a medicine name in
        # the original Colab review. Formula-like names are rejected here
        # because the RPA handoff rejects them too: an approved document
        # must never fail handoff validation.
        if is_placeholder_name(value):
            raise ValueError("Review/approval command is not a valid product name")
        return value

    @field_validator("ten_dich_vu", "don_vi_tinh")
    @classmethod
    def reject_formula_text(cls, value: str) -> str:
        if is_formula_like(value):
            raise ValueError("Text must not start with =, +, - or @ (spreadsheet formula)")
        return value

    @model_validator(mode="after")
    def check_total(self):
        if self.so_luong * self.don_gia != self.thanh_tien:
            raise ValueError("Quantity × unit price must equal total")
        return self


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_id: str = Field(min_length=2, max_length=80)
    reviewed_document_type: Literal["MEDICINE_INVOICE", "MEDICAL_PAYMENT"]
    items: list[ReviewedItem] = Field(min_length=1, max_length=MAX_ITEMS)
    confirm_names_and_values: bool
    # Required when the reviewed row count differs from the extracted one
    # (rows the extractor missed, or duplicate/garbage rows removed).
    row_count_change_reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_review(self):
        if not self.confirm_names_and_values:
            raise ValueError("Explicit human confirmation required")
        if len({it.stt for it in self.items}) != len(self.items):
            raise ValueError("Duplicate STT")
        for item in self.items:
            if self.reviewed_document_type == "MEDICAL_PAYMENT":
                if item.payment_details is None:
                    raise ValueError("Payment details required for each payment row")
                if item.payment_details.chi_phi != item.thanh_tien:
                    raise ValueError("Service total must equal chi_phi")
            elif item.payment_details is not None:
                raise ValueError("Payment details not allowed for medicine document")
        return self


class ApprovePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approver_id: str = Field(min_length=2, max_length=80)
    acknowledge_review: bool


def _review_changes(originals: list[dict], reviewed: list[dict]) -> dict:
    """Which fields the reviewer changed, by STT; field names only, no values (R-303)."""
    by_stt = {o.get("stt"): o for o in originals if isinstance(o, dict)}
    changed, added = [], []
    for item in reviewed:
        original = by_stt.get(item["stt"])
        if original is None:
            added.append(item["stt"])
            continue
        fields = [c for c in EXPORT_COLUMNS if original.get(c) != item[c]]
        if fields:
            changed.append({"stt": item["stt"], "fields": fields})
    reviewed_stts = {item["stt"] for item in reviewed}
    removed = [stt for stt in by_stt if stt not in reviewed_stts]
    return {"changed_rows": changed, "added_stts": added,
            "removed_original_stts": sorted(removed, key=str)}


def _decode_image(data: bytes, content_type: str | None) -> dict:
    """Validate an uploaded image; return the decoded RGB image plus what to store."""
    if content_type not in IMAGE_TYPES:
        raise HTTPException(415, "Only PNG, JPEG or WEBP supported")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "Image exceeds 8 MB")
    try:
        from PIL import Image
        image = Image.open(io.BytesIO(data))
        # Dimensions come from the header: reject before decoding pixels.
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image dimensions too large")
        image.load()
        image = image.convert("RGB")
    except Exception as exc:
        raise HTTPException(422, "Invalid image") from exc
    return {"image": image, "content_type": content_type, "data": data,
            "sha256": hashlib.sha256(data).hexdigest(), "width": image.width, "height": image.height}


def _store_image(con: sqlite3.Connection, document_id: str, upload: dict) -> dict:
    con.execute(
        "INSERT INTO document_images(document_id, content_type, sha256, width, height, data, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (document_id, upload["content_type"], upload["sha256"], upload["width"], upload["height"],
         upload["data"], _now()),
    )
    return {"sha256": upload["sha256"], "width": upload["width"], "height": upload["height"],
            "bytes": len(upload["data"])}


def _excel_safe(value: Any) -> Any:
    """Protect CSV consumers against spreadsheet formula injection."""
    if not isinstance(value, str):
        return value
    if re.match(r"^[\s\x00-\x1f]*[=+@-]", value):
        return "'" + value
    return value


def create_app(*, db_path: str | None = None, api_key: str | None = None,
               approver_api_key: str | None = None, store_images: bool | None = None) -> FastAPI:
    """Build the review API.

    `api_key` is the operator credential. When `approver_api_key` (or
    INVOICEFLOW_APPROVER_API_KEY) is set, only that key may approve and it
    may not import or review, so review and approval need two credentials.
    `store_images` (or INVOICEFLOW_STORE_IMAGES, default true) keeps uploaded
    images so reviewers can see the evidence behind each value.
    """
    db_path = db_path or os.environ.get("INVOICEFLOW_DB_PATH", DEFAULT_DB_PATH)
    api_key = api_key or os.environ.get("INVOICEFLOW_API_KEY")
    approver_api_key = approver_api_key or os.environ.get("INVOICEFLOW_APPROVER_API_KEY") or None
    if not api_key or len(api_key) < 16:
        raise RuntimeError("Set INVOICEFLOW_API_KEY to a random string of at least 16 characters")
    if approver_api_key is not None and (len(approver_api_key) < 16 or approver_api_key == api_key):
        raise RuntimeError("INVOICEFLOW_APPROVER_API_KEY must be at least 16 characters and differ from the operator key")
    if store_images is None:
        store_images = os.environ.get("INVOICEFLOW_STORE_IMAGES", "true").strip().lower() not in {
            "0", "false", "no", "off"}
    _init_db(db_path)

    app = FastAPI(title="InvoiceFlow AI Review API", version="0.6.0")
    app.state.db_path = db_path
    app.state.document_processor: Callable | None = None

    def authorize(x_api_key: str | None = Header(default=None)) -> str:
        """Return the caller's role: 'operator' or 'approver'."""
        if x_api_key is not None:
            if hmac.compare_digest(x_api_key, api_key):
                return "operator"
            if approver_api_key is not None and hmac.compare_digest(x_api_key, approver_api_key):
                return "approver"
        raise HTTPException(401, "Invalid API key")

    def require_operator(role: str = Depends(authorize)) -> str:
        if role != "operator":
            raise HTTPException(403, "Approver key cannot import or review documents")
        return role

    def require_approver(role: str = Depends(authorize)) -> str:
        if approver_api_key is not None and role != "approver":
            raise HTTPException(403, "Approval requires the approver API key")
        return role

    def store_result(result: dict, *, actor: str, source: str = "colab", upload: dict | None = None) -> dict:
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise HTTPException(422, "Invalid extraction result")
        if len(result["items"]) > MAX_ITEMS:
            raise HTTPException(422, "Too many extracted rows")
        try:
            # Limit potentially oversized/sensitive stored result payloads.
            raw_json = _as_json(result)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, "Extraction result is not valid JSON") from exc
        if len(raw_json.encode("utf-8")) > 2_000_000:
            raise HTTPException(413, "Extraction result too large")

        doc_id, t = uuid.uuid4().hex, _now()
        doc_type = str(result.get("document_type") or "UNKNOWN")[:80]
        qa = str(result.get("qa_status") or "")
        errors = qa.startswith("BLOCKED") or not result["items"]
        status = "BLOCKED_VALIDATION" if errors else "PENDING_REVIEW"
        with _conn(db_path) as con:
            con.execute(
                "INSERT INTO documents(id, status, original_document_type, raw_result_json, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (doc_id, status, doc_type, raw_json, t, t),
            )
            _record(con, doc_id, "IMPORTED", actor, {
                "source": source, "row_count": len(result["items"]),
                "original_status": str(result.get("status") or ""),
                "original_qa": qa,
            })
            if upload is not None:
                _record(con, doc_id, "IMAGE_STORED", actor, _store_image(con, doc_id, upload))
            return _public(_get(con, doc_id))

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "invoiceflow-review", "version": "0.6.0"}

    @app.post("/api/v1/documents/import", dependencies=[Depends(require_operator)], status_code=201)
    def import_document(payload: ImportPayload):
        # Never trust result.ready_for_rpa or LLM/model QA status for approval.
        return store_result(payload.result, actor="import", source=payload.source)

    @app.post("/api/v1/documents/upload", dependencies=[Depends(require_operator)], status_code=201)
    def upload_document(file: UploadFile = File(...)):
        if app.state.document_processor is None:
            raise HTTPException(503, "OCR pipeline not configured in this process")
        upload = _decode_image(file.file.read(MAX_UPLOAD_BYTES + 1), file.content_type)
        try:
            result = app.state.document_processor(upload["image"])
        except Exception as exc:
            # Do not leak patient data or OCR tokens via error response.
            raise HTTPException(500, "Extraction failed; see server logs") from exc
        return store_result(result, actor="upload", source="image_upload",
                            upload=upload if store_images else None)

    @app.put("/api/v1/documents/{document_id}/image", dependencies=[Depends(require_operator)],
             status_code=201)
    def attach_image(document_id: str, file: UploadFile = File(...)):  # noqa: B008 (FastAPI idiom)
        """Attach the source image to an imported result (e.g. from Colab), once."""
        if not store_images:
            raise HTTPException(403, "Image storage is disabled on this server")
        upload = _decode_image(file.file.read(MAX_UPLOAD_BYTES + 1), file.content_type)
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["has_image"]:
                raise HTTPException(409, "Document already has a source image")
            if row["status"] == "APPROVED":
                raise HTTPException(409, "Evidence cannot be added after approval")
            size = (_load_json(row["raw_result_json"]) or {}).get("image_size")
            # OCR box coordinates are only meaningful on the image OCR saw.
            if size and list(size) != [upload["width"], upload["height"]]:
                raise HTTPException(422, f"Image is {upload['width']}x{upload['height']}, "
                                         f"but OCR ran on {size[0]}x{size[1]}")
            _record(con, document_id, "IMAGE_ATTACHED", "operator", _store_image(con, document_id, upload))
            return _public(_get(con, document_id))

    @app.get("/api/v1/documents/{document_id}/image", dependencies=[Depends(authorize)])
    def get_image(document_id: str):
        with _conn(db_path) as con:
            _get(con, document_id)
            image = con.execute("SELECT content_type, sha256, data FROM document_images WHERE document_id=?",
                                (document_id,)).fetchone()
        if image is None:
            raise HTTPException(404, "No source image stored for this document")
        return Response(bytes(image["data"]), media_type=image["content_type"],
                        headers={"Cache-Control": "no-store", "X-Image-SHA256": image["sha256"]})

    @app.get("/api/v1/documents/{document_id}/evidence", dependencies=[Depends(authorize)])
    def get_evidence(document_id: str):
        """OCR boxes behind each extracted value, for drawing on the source image."""
        with _conn(db_path) as con:
            row = _get(con, document_id)
        return {**build_evidence(_load_json(row["raw_result_json"]) or {}), "has_image": bool(row["has_image"])}

    @app.get("/api/v1/documents", dependencies=[Depends(authorize)])
    def list_documents(limit: int = 20):
        if not 1 <= limit <= 100:
            raise HTTPException(422, "limit must be between 1 and 100")
        with _conn(db_path) as con:
            rows = con.execute("SELECT id,status,original_document_type,reviewed_document_type,created_at,updated_at "
                               "FROM documents ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
            return [{**dict(row), "ready_for_rpa": row["status"] == "APPROVED"} for row in rows]

    @app.get("/api/v1/documents/{document_id}", dependencies=[Depends(authorize)])
    def get_document(document_id: str):
        with _conn(db_path) as con:
            return _public(_get(con, document_id))

    @app.get("/api/v1/documents/{document_id}/audit", dependencies=[Depends(authorize)])
    def audit(document_id: str):
        with _conn(db_path) as con:
            _get(con, document_id)
            rows = con.execute(
                "SELECT action,actor,details_json,created_at FROM audit_events "
                "WHERE document_id=? ORDER BY id", (document_id,),
            ).fetchall()
            return [{"action": r["action"], "actor": r["actor"],
                     "details": _load_json(r["details_json"]),
                     "created_at": r["created_at"]} for r in rows]

    @app.put("/api/v1/documents/{document_id}/review", dependencies=[Depends(require_operator)])
    def review(document_id: str, payload: ReviewPayload):
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] not in {"PENDING_REVIEW", "BLOCKED_VALIDATION", "REVIEWED"}:
                raise HTTPException(409, "Document cannot be reviewed in current status")
            originals = _load_json(row["raw_result_json"]).get("items", [])
            # Rows are never dropped or added silently: a different row count
            # (missed rows, garbage rows, or an extraction with no rows at all)
            # needs a written reason, which goes to the audit trail.
            reason = (payload.row_count_change_reason or "").strip()
            if len(payload.items) != len(originals) and len(reason) < MIN_REASON_LENGTH:
                raise HTTPException(422, "Reviewed row count differs from extracted row count; "
                                         f"give row_count_change_reason (at least {MIN_REASON_LENGTH} characters)")
            # Reviewer can correct STT, name, money and classify UNKNOWN after inspection.
            reviewed_items = [it.model_dump() for it in payload.items]
            reviewed_json = _as_json(reviewed_items)
            con.execute(
                "UPDATE documents SET reviewed_items_json=?, reviewed_document_type=?, "
                "reviewer_id=?, status='REVIEWED', review_version=review_version+1, "
                "approver_id=NULL, approved_payload_sha256=NULL, updated_at=? WHERE id=?",
                (reviewed_json, payload.reviewed_document_type,
                 payload.reviewer_id, _now(), document_id),
            )
            details = {
                "row_count": len(payload.items), "extracted_row_count": len(originals),
                "reviewed_document_type": payload.reviewed_document_type,
                "review_version": row["review_version"] + 1,
                **_review_changes(originals, reviewed_items),
            }
            if len(payload.items) != len(originals):
                details["row_count_change_reason"] = reason
            _record(con, document_id, "REVIEW_SUBMITTED", payload.reviewer_id, details)
            return _public(_get(con, document_id))

    @app.post("/api/v1/documents/{document_id}/approve")
    def approve(document_id: str, payload: ApprovePayload, role: str = Depends(require_approver)):
        if not payload.acknowledge_review:
            raise HTTPException(422, "Explicit approval acknowledgement required")
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] != "REVIEWED":
                raise HTTPException(409, "A completed human review is required before approval")
            # Four-eyes rule: the person who corrected the data cannot release it.
            if payload.approver_id.strip().casefold() == (row["reviewer_id"] or "").strip().casefold():
                raise HTTPException(403, "Approver must be a different person than the reviewer")
            data = _load_json(row["reviewed_items_json"])
            if not data:
                raise HTTPException(409, "No reviewed items")
            # Revalidate persisted reviewed data before release.
            try:
                ReviewPayload(reviewer_id=row["reviewer_id"],
                              reviewed_document_type=row["reviewed_document_type"],
                              confirm_names_and_values=True, items=data)
            except Exception as exc:
                raise HTTPException(409, "Stored review failed validation") from exc
            digest = payload_sha256(data)
            con.execute("UPDATE documents SET status='APPROVED', approver_id=?, "
                        "approved_payload_sha256=?, updated_at=? WHERE id=?",
                        (payload.approver_id, digest, _now(), document_id))
            _record(con, document_id, "APPROVED", payload.approver_id, {
                "row_count": len(data), "export_eligible": True, "role": role,
                "review_version": row["review_version"], "payload_sha256": digest,
            })
            return _public(_get(con, document_id))

    @app.get("/api/v1/documents/{document_id}/export.csv")
    def export_csv(document_id: str, role: str = Depends(authorize)):
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] != "APPROVED":
                raise HTTPException(409, "Document requires approval before export")
            items = _load_json(row["reviewed_items_json"])
            digest = payload_sha256(items)
            # Detect stored data changed after approval (e.g. edited in the DB file).
            if not row["approved_payload_sha256"] or not hmac.compare_digest(digest, row["approved_payload_sha256"]):
                raise HTTPException(409, "Approved payload hash mismatch: manual investigation required")
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\r\n")
            writer.writerow(EXPORT_COLUMNS)
            for item in items:
                writer.writerow([_excel_safe(item[c]) for c in EXPORT_COLUMNS])
            _record(con, document_id, "CSV_EXPORTED", role, {"row_count": len(items), "payload_sha256": digest})
            csv_payload = output.getvalue()
            return Response(csv_payload.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="invoiceflow_{document_id}.csv"',
                                     "Cache-Control": "no-store",
                                     "X-Payload-SHA256": digest})

    return app
