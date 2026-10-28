"""Optional local UI: import, review, approve, export via the FastAPI backend.

Never captures a secret in client logs. Do not deploy publicly without proper
OIDC/RBAC, TLS, protected storage and privacy controls.
"""
from __future__ import annotations

import io
import os

import httpx
import pandas as pd
import streamlit as st

st.set_page_config(page_title="InvoiceFlow AI | Review", page_icon="🧾", layout="wide")
st.title("🧾 InvoiceFlow AI")
st.caption("OCR → Document Router → KIE → Human Review → Approval → CSV handoff | Local demo")
st.warning("Demo only. Use synthetic/de-identified documents. Never expose the local API publicly.")

api_url = os.getenv("INVOICEFLOW_API_URL", "http://127.0.0.1:8000").rstrip("/")
api_key = os.getenv("INVOICEFLOW_API_KEY", "")
# Optional second credential; when the server has one configured, only it can approve.
approver_key = os.getenv("INVOICEFLOW_APPROVER_API_KEY", "")
if not api_key:
    st.error("Set INVOICEFLOW_API_KEY in your local environment before starting the UI.")
    st.stop()
headers = {"X-API-Key": api_key}


def api(method: str, path: str, *, key: str | None = None, **kwargs):
    try:
        with httpx.Client(base_url=api_url, timeout=120) as conn:
            response = conn.request(method, path, headers={"X-API-Key": key or api_key}, **kwargs)
            if response.status_code >= 400:
                st.error(f"API error {response.status_code}: {response.text[:600]}")
                return None
            return response
    except httpx.HTTPError as exc:
        st.error(f"Could not reach API: {type(exc).__name__}")
        return None


with st.sidebar:
    st.subheader("API")
    st.code(api_url)
    health = api("GET", "/health")
    st.success("Backend online") if health is not None else st.error("Backend offline")
    tab = st.radio("Workflow", ["Upload", "Documents", "Review & approve", "Audit"])

if tab == "Upload":
    st.header("Upload de-identified document")
    st.info("Requires INVOICEFLOW_ENABLE_OCR_UPLOAD=true and OCR extra installed on API server. UNKNOWN layouts are never sent to LLM by this endpoint.")
    image = st.file_uploader("PNG, JPEG or WEBP", type=["png", "jpg", "jpeg", "webp"])
    if image is not None:
        content = image.getvalue()
        st.image(content, caption="Preview (local browser only)", width=520)
        if st.button("Extract and save", type="primary"):
            with st.spinner("Running local OCR..."):
                response = api("POST", "/api/v1/documents/upload",
                               files={"file": (image.name, content, image.type)})
            if response:
                doc = response.json()
                st.success(f"Saved: {doc['id']} / {doc['status']}")
                st.session_state["selected_document"] = doc["id"]

elif tab == "Documents":
    st.header("Stored documents")
    response = api("GET", "/api/v1/documents")
    if response:
        df = pd.DataFrame(response.json())
        st.dataframe(df, use_container_width=True, hide_index=True)
        if not df.empty:
            st.session_state["selected_document"] = st.selectbox("Document ID", df["id"].tolist())

elif tab == "Review & approve":
    st.header("Human review")
    doc_id = st.text_input("Document ID", value=st.session_state.get("selected_document", ""))
    if not doc_id:
        st.stop()
    response = api("GET", f"/api/v1/documents/{doc_id}")
    if response is None:
        st.stop()
    doc = response.json()
    st.write(f"**Status:** {doc['status']} · **Detected type:** {doc['original_document_type']}")
    st.info("Confirm every value against the ORIGINAL IMAGE before submitting. The backend does not store the image, so open your local source file separately. This UI cannot verify a name for you.")
    orig = doc.get("reviewed_items") or doc["raw_result"].get("items", [])
    cols = ["stt", "ten_dich_vu", "so_luong", "don_vi_tinh", "don_gia", "thanh_tien"]
    if not orig:
        st.warning("No rows were extracted. Add every row from the source image and give a reason below.")
    items = []
    for r in orig:
        details = r.get("payment_details")
        if isinstance(details, dict):
            # Exclude extractor-only `calculated_patient_payment`: the API
            # intentionally accepts only four validated payment fields.
            details = {k: details.get(k) for k in
                       ("chi_phi", "bhyt_chi", "so_giam", "benh_nhan_thanh_toan")}
        items.append({**{k: r.get(k) for k in cols}, "payment_details": details})
    df = pd.DataFrame([{k: v for k, v in row.items() if k != "payment_details"} for row in items],
                      columns=cols)
    edited = st.data_editor(df, hide_index=True, use_container_width=True, num_rows="dynamic",
                            key=f"review_editor_{doc_id}")
    reason = ""
    if len(edited) != len(orig):
        reason = st.text_input("Why does the row count differ from the extraction? (required)")
    reviewer = st.text_input("Reviewer ID", value="demo_operator")
    dtype = st.selectbox("Verified document type", ["MEDICINE_INVOICE", "MEDICAL_PAYMENT"],
                         index=1 if doc.get("reviewed_document_type") == "MEDICAL_PAYMENT" else 0)
    reviewed = st.checkbox("I independently verified ALL displayed values against the source image")
    if doc["status"] in {"PENDING_REVIEW", "BLOCKED_VALIDATION", "REVIEWED"} and st.button("Submit human review"):
        if not reviewed:
            st.error("You must verify and confirm all fields.")
        else:
            cleaned = edited.to_dict(orient="records")
            empty = [str(i + 1) for i, row in enumerate(cleaned)
                     if any(pd.isna(row.get(f)) or row.get(f) == "" for f in cols)]
            if empty:
                st.error(f"Fill every cell before submitting (rows {', '.join(empty)}).")
                st.stop()
            try:
                for index, row in enumerate(cleaned):
                    for field in ("stt", "so_luong", "don_gia", "thanh_tien"):
                        number = float(row[field])
                        if not number.is_integer():
                            raise ValueError(field)
                        row[field] = int(number)
                    source = items[index]["payment_details"] if index < len(items) else None
                    row["payment_details"] = source if dtype == "MEDICAL_PAYMENT" else None
            except (TypeError, ValueError):
                st.error("STT, quantity, unit price and amount must be whole numbers.")
                st.stop()
            body = {"reviewer_id": reviewer, "reviewed_document_type": dtype,
                    "confirm_names_and_values": True, "items": cleaned}
            if reason:
                body["row_count_change_reason"] = reason
            response = api("PUT", f"/api/v1/documents/{doc_id}/review", json=body)
            if response:
                st.success("Review saved. A different person must approve it.")
    if doc["status"] == "REVIEWED":
        st.divider()
        st.subheader("Approval — separate action")
        st.dataframe(pd.DataFrame(doc["reviewed_items"]), use_container_width=True)
        st.caption(f"Reviewed by {doc['reviewer_id']}. Approval must come from a different person.")
        approver = st.text_input("Approver ID", key=f"approver_{doc_id}")
        approve = st.checkbox("I inspected the REVIEWED database version and authorize CSV export", key=f"approve_{doc_id}")
        if st.button("Approve reviewed document", disabled=not (approve and approver.strip())):
            response = api("POST", f"/api/v1/documents/{doc_id}/approve", key=approver_key or None, json={
                "approver_id": approver, "acknowledge_review": True,
            })
            if response:
                st.success("Approved. Reload this document to download CSV.")
    if doc["status"] == "APPROVED":
        st.success("Approved — export eligible")
        response = api("GET", f"/api/v1/documents/{doc_id}/export.csv")
        if response:
            st.download_button("Download approved CSV", response.content,
                               file_name=f"invoiceflow_{doc_id}.csv", mime="text/csv")

else:
    st.header("Audit trail")
    doc_id = st.text_input("Document ID", value=st.session_state.get("selected_document", ""))
    if doc_id:
        response = api("GET", f"/api/v1/documents/{doc_id}/audit")
        if response:
            st.dataframe(pd.DataFrame(response.json()), use_container_width=True)
