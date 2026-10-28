"""InvoiceFlow AI Module 05B — APPROVED CSV handoff + local ERP simulator.

This module does NOT operate a real RPA bot or modify an external ERP.
It reads an approved CSV through the 05A API, validates and snapshots the
payload, then supports an idempotent local SQLite test worker.

Sensitive document data should not be stored in real deployments without
appropriate privacy/security protections. Local demonstration only.
"""
from __future__ import annotations

import csv
import hmac
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .validation import EXPORT_COLUMNS, is_formula_like, is_placeholder_name, payload_sha256

COLUMNS = EXPORT_COLUMNS
MAX_CSV_BYTES = 2_000_000
MAX_ROWS = 200

class HandoffError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _connection(db_path: str) -> Iterator[sqlite3.Connection]:
    """One transaction per block: commit on success, roll back on error, always close."""
    con = sqlite3.connect(db_path, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        with con:
            yield con
    finally:
        con.close()


def init_handoff_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connection(db_path) as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""CREATE TABLE IF NOT EXISTS rpa_jobs (
            document_id TEXT PRIMARY KEY,
            payload_sha256 TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('PENDING','SIMULATED')),
            rows_json TEXT NOT NULL,
            submitted_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS erp_demo_rows (
            document_id TEXT NOT NULL,
            stt INTEGER NOT NULL,
            ten_dich_vu TEXT NOT NULL,
            so_luong INTEGER NOT NULL,
            don_vi_tinh TEXT NOT NULL,
            don_gia INTEGER NOT NULL,
            thanh_tien INTEGER NOT NULL,
            inserted_at TEXT NOT NULL,
            PRIMARY KEY(document_id, stt)
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS rpa_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id TEXT NOT NULL,
            event TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )""")


def parse_approved_csv(csv_bytes: bytes) -> list[dict[str, Any]]:
    """Reject malformed or unsafe CSV rather than guessing/repairing fields."""
    if not isinstance(csv_bytes, bytes) or len(csv_bytes) > MAX_CSV_BYTES:
        raise HandoffError("Invalid CSV payload size")
    try:
        content = csv_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content, newline=""), strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
            raise HandoffError("Unexpected CSV columns or column order")
        raw_rows = list(reader)
    except UnicodeError as exc:
        raise HandoffError("Invalid CSV encoding") from exc
    except csv.Error as exc:
        raise HandoffError("Malformed CSV") from exc
    if not 1 <= len(raw_rows) <= MAX_ROWS:
        raise HandoffError("CSV must contain between 1 and 200 rows")

    seen = set()
    rows = []
    for raw in raw_rows:
        if any(v is None for v in raw.values()) or None in raw:
            raise HandoffError("CSV row shape is invalid")
        for field in ("stt", "so_luong", "don_gia", "thanh_tien"):
            if not re.fullmatch(r"[0-9]+", (raw.get(field) or "").strip()):
                raise HandoffError(f"Invalid integer in {field}")
        stt = int(raw["stt"])
        qty = int(raw["so_luong"])
        price = int(raw["don_gia"])
        total = int(raw["thanh_tien"])
        name = raw["ten_dich_vu"].strip()
        unit = raw["don_vi_tinh"].strip()
        if stt < 1 or qty < 1 or stt in seen:
            raise HandoffError("Invalid or duplicate STT / quantity")
        seen.add(stt)
        if not name or is_placeholder_name(name) or len(name) > 500:
            raise HandoffError("Invalid product/service name")
        if not unit or len(unit) > 40:
            raise HandoffError("Invalid unit")
        # Same rule the backend applies at review, so an approved row passes;
        # also catches the exporter's quote prefix.
        if is_formula_like(name) or is_formula_like(unit):
            raise HandoffError("Potential spreadsheet formula injection")
        if price * qty != total:
            raise HandoffError(f"Arithmetic mismatch in row {stt}")
        if price > 10**12 or total > 10**15:
            raise HandoffError("Numeric value exceeds demo limits")
        rows.append({
            "stt": stt,
            "ten_dich_vu": name,
            "so_luong": qty,
            "don_vi_tinh": unit,
            "don_gia": price,
            "thanh_tien": total,
        })
    return rows


def _snapshot(row: sqlite3.Row, *, action: str) -> dict[str, Any]:
    return {
        "document_id": row["document_id"],
        "state": row["state"],
        "row_count": len(json.loads(row["rows_json"])),
        "payload_sha256": row["payload_sha256"],
        "action": action,
    }


def submit_approved_job(*, client, api_headers: dict, document_id: str, db_path: str) -> dict:
    """Read from authoritative API. Never accept unsanctioned notebook item lists."""
    if not re.fullmatch(r"[a-f0-9]{32}", document_id):
        raise HandoffError("Invalid document ID")
    root = f"/api/v1/documents/{document_id}"
    status_response = client.get(root, headers=api_headers)
    if status_response.status_code != 200:
        raise HandoffError(f"Document lookup failed: HTTP {status_response.status_code}")
    document = status_response.json()
    if document.get("status") != "APPROVED" or document.get("ready_for_rpa") is not True:
        raise HandoffError("Only APPROVED documents may enter the handoff queue")
    export_response = client.get(f"{root}/export.csv", headers=api_headers)
    if export_response.status_code != 200:
        raise HandoffError(f"Approved CSV export failed: HTTP {export_response.status_code}")
    rows = parse_approved_csv(export_response.content)
    serialized = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = payload_sha256(rows)
    # The backend hashed the payload at approval; the CSV received must match it.
    approved_digest = document.get("approved_payload_sha256")
    header_digest = export_response.headers.get("X-Payload-SHA256")
    if not approved_digest or not header_digest:
        raise HandoffError("Backend did not provide the approved payload hash")
    if not (hmac.compare_digest(digest, approved_digest) and hmac.compare_digest(digest, header_digest)):
        raise HandoffError("Exported CSV does not match the approved payload hash")

    init_handoff_db(db_path)
    with _connection(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        existing = con.execute("SELECT * FROM rpa_jobs WHERE document_id=?", (document_id,)).fetchone()
        if existing:
            if existing["payload_sha256"] != digest:
                raise HandoffError("Document payload changed after initial handoff: manual investigation required")
            return _snapshot(existing, action="ALREADY_SUBMITTED")
        stamp = _now()
        con.execute(
            "INSERT INTO rpa_jobs(document_id,payload_sha256,state,rows_json,submitted_at,updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (document_id, digest, "PENDING", serialized, stamp, stamp),
        )
        con.execute("INSERT INTO rpa_audit(document_id,event,timestamp) VALUES (?,?,?)",
                    (document_id, "HANDOFF_ACCEPTED", stamp))
        stored = con.execute("SELECT * FROM rpa_jobs WHERE document_id=?", (document_id,)).fetchone()
        return _snapshot(stored, action="CREATED")


def run_demo_worker(*, document_id: str, db_path: str) -> dict:
    """Writes approved rows to a LOCAL ERP SIMULATOR; no remote RPA execution."""
    init_handoff_db(db_path)
    with _connection(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM rpa_jobs WHERE document_id=?", (document_id,)).fetchone()
        if row is None:
            raise HandoffError("Handoff job not found")
        if row["state"] == "SIMULATED":
            return _snapshot(row, action="ALREADY_SIMULATED")
        if row["state"] != "PENDING":
            raise HandoffError("Unsupported job state")
        rows = json.loads(row["rows_json"])
        for item in rows:
            con.execute("""INSERT INTO erp_demo_rows(
                document_id,stt,ten_dich_vu,so_luong,don_vi_tinh,don_gia,thanh_tien,inserted_at)
                VALUES(?,?,?,?,?,?,?,?)""", (
                    document_id, item["stt"], item["ten_dich_vu"], item["so_luong"],
                    item["don_vi_tinh"], item["don_gia"], item["thanh_tien"], _now(),
                ))
        con.execute("UPDATE rpa_jobs SET state='SIMULATED',updated_at=? WHERE document_id=?", (_now(), document_id))
        con.execute("INSERT INTO rpa_audit(document_id,event,timestamp) VALUES(?,?,?)",
                    (document_id, "LOCAL_ERP_SIMULATED", _now()))
        result = con.execute("SELECT * FROM rpa_jobs WHERE document_id=?", (document_id,)).fetchone()
        return _snapshot(result, action="SIMULATED_NOW")


def get_demo_erp_rows(*, document_id: str, db_path: str) -> list[dict]:
    init_handoff_db(db_path)
    with _connection(db_path) as con:
        records = con.execute(
            "SELECT stt,ten_dich_vu,so_luong,don_vi_tinh,don_gia,thanh_tien "
            "FROM erp_demo_rows WHERE document_id=? ORDER BY stt", (document_id,)
        ).fetchall()
        return [dict(rec) for rec in records]


def get_handoff_events(*, document_id: str, db_path: str) -> list[dict]:
    init_handoff_db(db_path)
    with _connection(db_path) as con:
        records = con.execute(
            "SELECT event,timestamp FROM rpa_audit WHERE document_id=? ORDER BY id", (document_id,)
        ).fetchall()
        return [dict(rec) for rec in records]
