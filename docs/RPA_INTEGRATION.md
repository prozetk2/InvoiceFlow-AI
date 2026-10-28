# RPA handoff — from local simulator to Power Automate or UiPath

## Current completed functionality

- Reads backend-approved CSV only.
- Validates fields and quantity × unit price.
- Computes SHA-256 over canonical structured payload and requires it to equal the hash the backend stored at approval (`approved_payload_sha256`, `X-Payload-SHA256`).
- Creates unique job for `document_id` transactionally.
- Rejects changed payload for same ID.
- Runs an **idempotent local SQLite ERP simulator**, not a real RPA bot.
- Writes audit events `HANDOFF_ACCEPTED`, `LOCAL_ERP_SIMULATED`.

## Operator command

```bash
export INVOICEFLOW_API_URL=http://127.0.0.1:8000
export INVOICEFLOW_API_KEY=your-local-secret
python scripts/rpa_handoff_cli.py APPROVED_32_HEX_ID --simulate
```

On Windows PowerShell:

```powershell
$env:INVOICEFLOW_API_URL = "http://127.0.0.1:8000"
$env:INVOICEFLOW_API_KEY = "your-local-secret"
python scripts/rpa_handoff_cli.py APPROVED_32_HEX_ID --simulate
```

## Future adapter contract

A real adapter must accept an immutable **approved job snapshot**, not arbitrary image, unreviewed LLM text or ad-hoc CSV. Proposed interface:

```python
class ERPAdapter:
    def submit(self, *, document_id: str, payload_sha256: str, rows: list[dict]) -> dict:
        """Return external job ID and state; no silent partial success."""
        raise NotImplementedError
```

Before enabling an adapter:

1. Secure server-to-agent authentication and role assignment.
2. Use a dedicated test tenant/sandbox, not production accounting/ERP.
3. Map field names/validation to the target form.
4. Deduplicate by the immutable document+payload key **in the target system**, not only in local SQLite.
5. Define retries, timeout, partial failure resolution and compensation.
6. Capture screenshots/audit evidence only after redacting sensitive data.
7. Verify per-row acknowledgement before declaring completion.
8. Add a human abort/resume control and access logs.
9. Test concurrent double-submission, network timeouts, and stale approvals.
10. Require a final release sign-off for the specific target environment.

## Power Automate Desktop candidate workflow (not implemented)

`Receive approved job → open test ERP desktop/web form → enter line items → verify saved values → record remote receipt → mark processed or errored`.

The same conceptual flow applies to UiPath. Do not claim real integration until the adapter has been implemented and observed on an isolated target system.
