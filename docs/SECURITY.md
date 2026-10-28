# Security, privacy and data governance

## Data classification

Even a patient-free-looking receipt might contain personal information: patient name, dates, identifiers, examination names, prescription quantities, billing information or phone numbers. OCR *text* and OCR *coordinates* remain sensitive. Model confidence values do not imply safe public release.

## Controls actually present

- Read-only/default **review-only API** unless upload OCR is explicitly configured.
- `X-API-Key` check using constant-time comparison.
- Limited image MIME types, upload bytes, pixel count, extraction rows and serialized result size.
- Strong Pydantic checks for human-reviewed fields and integer-VND arithmetic.
- Explicit review and approval steps with audit records.
- No approved CSV export prior to persisted `APPROVED` status.
- Csv formula-injection escaping and idempotent handoff checks.
- No external Gemini API call from the default HTTP upload handler.
- OCR text treated as untrusted prompt data.
- Uploaded source images are stored as review evidence in the same local SQLite file (`document_images`), served only with an API key and `Cache-Control: no-store`. Audit events record the image SHA-256 and size, never its bytes. Set `INVOICEFLOW_STORE_IMAGES=false` to keep no images (reviewers then check the source file themselves).
- Evaluation reports list missed STTs and wrong field names only, never names or amounts.

## Controls NOT present (must be addressed before production)

- Identity federation/OIDC and granular RBAC (reviewer ≠ approver is enforced by ID and an optional second API key, but IDs are not bound to a login).
- Full audit immutability or SIEM integration.
- Encrypted patient data at rest (including stored source images), protected backup keys or retention policies.
- Privacy-compliant PHI redaction/DLP with measurable recall.
- Endpoint rate limits, upload malware scanning, reverse-proxy WAF/HTTPS in the dev server.
- Request-level model disclosure consent and independent data-controller review.
- Queue isolation, workload sandboxing, distributed locks and operational resilience.
- Clinical or billing regulatory conformance assessment.
- A secure enterprise RPA connector.

## Safe public GitHub hygiene

1. `git status --short` before committing.
2. Inspect staged content with `git diff --cached --stat` and `git diff --cached`.
3. Do not push `.env`, API keys, SSH PEMs, local `*.sqlite3`, notebook cell outputs, Colab archives, raw images or captured console snippets with patient information.
4. Only use the generated `examples/synthetic_extraction.json` and synthetic tests as public fixtures.
5. If a secret accidentally enters Git history, rotate it, revoke access and coordinate history cleanup; deleting the working file is insufficient.

## Threat scenarios

| Threat | Current mitigation | Remaining action |
| --- | --- | --- |
| Prompt injection from scanned document | OCR untrusted, JSON escaped and OCR evidence validator | Harden prompt/context length, perform additional red-team testing |
| LLM invents amount | Numeric evidence must be present; QA blocks mismatch | Evaluate on independent adversarial sets |
| Misleading OCR confidence | Names remain unverified | UI highlighting and independent human verification |
| Staff mistakes `REVIEW` for a medication name | Server-side review validator rejects placeholder names | Extend contextual placeholder detection and automated tests |
| Unapproved export | HTTP 409 unless APPROVED; approver ≠ reviewer; approved-payload hash checked at export and handoff | OIDC identities and RBAC |
| CSV formula injection | Export prefix escaping, handoff rejects suspicious formulas | Further test with target spreadsheet client |
| Repeated bot execution | Immutable content hash, unique DB key, SQLite transaction | Distributed idempotency token for remote ERP |
| External data disclosure | Gemini off by default, explicit CLI opt-in | DLP/consent, provider contracts and logging policy |

## Disclosure / deployment statement

**InvoiceFlow AI is not a certified health information system.** The source repository and demos are intended only for synthetic/de-identified data and technical evaluation; do not process personal health data under an unreviewed deployment.
