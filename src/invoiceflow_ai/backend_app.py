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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 200


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


def _conn(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


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


def _record(con: sqlite3.Connection, doc_id: str, action: str,
            actor: str, details: dict | None = None) -> None:
    con.execute(
        "INSERT INTO audit_events(document_id, action, actor, details_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (doc_id, action, actor, _as_json(details or {}), _now()),
    )


def _get(con: sqlite3.Connection, document_id: str) -> sqlite3.Row:
    row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
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
    def reject_confirmation_placeholders(cls, value: str) -> str:
        # A literal operator command accidentally became a medicine name in
        # the original Colab review. CSV-formula-like names remain allowed at
        # review stage for compatibility and are escaped at CSV export.
        if value.casefold() in {"review", "approve", "test", "placeholder", "unknown", "n/a"}:
            raise ValueError("Review/approval command is not a valid product name")
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
    reviewer_id: str = Field(min_length=2, max_length=80)
    acknowledge_review: bool


def _excel_safe(value: Any) -> Any:
    """Protect CSV consumers against spreadsheet formula injection."""
    if not isinstance(value, str):
        return value
    if re.match(r"^[\s\x00-\x1f]*[=+@-]", value):
        return "'" + value
    return value


def create_app(*, db_path: str | None = None, api_key: str | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("INVOICEFLOW_DB_PATH", "/content/invoiceflow_demo.sqlite3")
    api_key = api_key or os.environ.get("INVOICEFLOW_API_KEY")
    if not api_key or len(api_key) < 16:
        raise RuntimeError("Set INVOICEFLOW_API_KEY to a random string of at least 16 characters")
    _init_db(db_path)

    app = FastAPI(title="InvoiceFlow AI Review API", version="0.5.0")
    app.state.db_path = db_path
    app.state.document_processor: Callable | None = None

    def authorize(x_api_key: str | None = Header(default=None)):
        if x_api_key is None or not hmac.compare_digest(x_api_key, api_key):
            raise HTTPException(401, "Invalid API key")

    def store_result(result: dict, *, actor: str, source: str = "colab") -> dict:
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
            return _public(_get(con, doc_id))

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "invoiceflow-review", "version": "0.5.0"}

    @app.post("/api/v1/documents/import", dependencies=[Depends(authorize)], status_code=201)
    def import_document(payload: ImportPayload):
        # Never trust result.ready_for_rpa or LLM/model QA status for approval.
        return store_result(payload.result, actor="import", source=payload.source)

    @app.post("/api/v1/documents/upload", dependencies=[Depends(authorize)], status_code=201)
    def upload_document(file: UploadFile = File(...)):
        if app.state.document_processor is None:
            raise HTTPException(503, "OCR pipeline not configured in this process")
        if file.content_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise HTTPException(415, "Only PNG, JPEG or WEBP supported")
        data = file.file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Image exceeds 8 MB")
        try:
            from PIL import Image, UnidentifiedImageError
            Image.MAX_IMAGE_PIXELS = 16_000_000
            image = Image.open(io.BytesIO(data))
            image.load()
            if image.width * image.height > 16_000_000:
                raise ValueError("Image dimensions too large")
            image = image.convert("RGB")
        except Exception as exc:
            raise HTTPException(422, "Invalid image") from exc
        try:
            result = app.state.document_processor(image)
        except Exception as exc:
            # Do not leak patient data or OCR tokens via error response.
            raise HTTPException(500, "Extraction failed; see server logs") from exc
        return store_result(result, actor="upload", source="image_upload")

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

    @app.put("/api/v1/documents/{document_id}/review", dependencies=[Depends(authorize)])
    def review(document_id: str, payload: ReviewPayload):
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] not in {"PENDING_REVIEW", "BLOCKED_VALIDATION", "REVIEWED"}:
                raise HTTPException(409, "Document cannot be reviewed in current status")
            originals = _load_json(row["raw_result_json"]).get("items", [])
            # Can't silently drop rows; explicit human corrections must cover all.
            if len(payload.items) != len(originals) or not originals:
                raise HTTPException(422, "Reviewed row count must equal extracted row count")
            # Reviewer can correct STT, name, money and classify UNKNOWN after inspection.
            reviewed_json = _as_json([it.model_dump() for it in payload.items])
            con.execute(
                "UPDATE documents SET reviewed_items_json=?, reviewed_document_type=?, "
                "reviewer_id=?, status='REVIEWED', updated_at=? WHERE id=?",
                (reviewed_json, payload.reviewed_document_type,
                 payload.reviewer_id, _now(), document_id),
            )
            _record(con, document_id, "REVIEW_SUBMITTED", payload.reviewer_id, {
                "row_count": len(payload.items), "reviewed_document_type": payload.reviewed_document_type,
            })
            return _public(_get(con, document_id))

    @app.post("/api/v1/documents/{document_id}/approve", dependencies=[Depends(authorize)])
    def approve(document_id: str, payload: ApprovePayload):
        if not payload.acknowledge_review:
            raise HTTPException(422, "Explicit approval acknowledgement required")
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] != "REVIEWED":
                raise HTTPException(409, "A completed human review is required before approval")
            if row["reviewer_id"] != payload.reviewer_id:
                raise HTTPException(403, "Approval reviewer must match submitted reviewer")
            data = _load_json(row["reviewed_items_json"])
            if not data:
                raise HTTPException(409, "No reviewed items")
            # Revalidate persisted reviewed data before release.
            try:
                ReviewPayload(reviewer_id=payload.reviewer_id,
                              reviewed_document_type=row["reviewed_document_type"],
                              confirm_names_and_values=True, items=data)
            except Exception as exc:
                raise HTTPException(409, "Stored review failed validation") from exc
            con.execute("UPDATE documents SET status='APPROVED',updated_at=? WHERE id=?",
                        (_now(), document_id))
            _record(con, document_id, "APPROVED", payload.reviewer_id,
                    {"row_count": len(data), "export_eligible": True})
            return _public(_get(con, document_id))

    @app.get("/api/v1/documents/{document_id}/export.csv", dependencies=[Depends(authorize)])
    def export_csv(document_id: str):
        with _conn(db_path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = _get(con, document_id)
            if row["status"] != "APPROVED":
                raise HTTPException(409, "Document requires approval before export")
            items = _load_json(row["reviewed_items_json"])
            output = io.StringIO()
            writer = csv.writer(output, lineterminator="\r\n")
            cols = ["stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien"]
            writer.writerow(cols)
            for item in items:
                writer.writerow([_excel_safe(item[c]) for c in cols])
            _record(con, document_id, "CSV_EXPORTED", "api_key_user", {"row_count": len(items)})
            csv_payload = output.getvalue()
            return Response(csv_payload.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                            headers={"Content-Disposition": f'attachment; filename="invoiceflow_{document_id}.csv"',
                                     "Cache-Control": "no-store"})

    return app
