# Spec 0001: Invoice processing for a multi-entity finance team
Status: draft (awaiting product-owner review). Date: 2026-11-20.
Produced by: Claude Opus 5.5, written in-session following the `/spec` instructions (`.claude/commands/spec.md`); the command was not run separately.
Inputs: `intent.md` at commit `e00fad8`; skills at commit `9443168`
(`gst-invoice-rules`, `data-protection`, `approval-and-audit`, `ux-standards`).

Requirement IDs (FR-n, NFR-n) are referenced from plan.md, tickets, tests and PRs.

---

## 1. Answers to the intent's open questions

| # | Question | Proposed answer | Status |
|---|---|---|---|
| 1 | How is an invoice assigned to an entity? | Each entity has one or more **GST registrations** (one GSTIN per state). The buyer GSTIN extracted from the invoice is matched to a registration, which gives the entity. Fallback: the default entity of the inbox or folder it came from. Otherwise the clerk picks one. A conflict (buyer GSTIN says entity A, inbox says B) raises a `warn`. | Proposed: PO to confirm |
| 2 | What is a near-duplicate? Can it be overridden? | See FR-30..33. Exact = same supplier GSTIN + normalised number + financial year (`block`), or identical file hash. Near = same supplier and either (a) normalised numbers 1 edit apart, or (b) total within 1% and date within 30 days (`warn`). A `block` can be overridden with a reason by a user with `flags:override`. | Proposed: PO to confirm thresholds |
| 3 | Can one person approve two tiers? | **No.** Each tier needs a distinct person (approval-and-audit §2). | Decided by policy; see concern C4 |
| 4 | Export column layout? | A generic, documented CSV and JSON format (FR-50), stable and versioned. Accounting-system-specific layouts (Tally, Zoho Books) come in later intents. | Carried forward: accounting team to name a target |
| 5 | Vendor master source? | Built automatically: a vendor is created on an invoice's first approval, keyed by GSTIN, and admins can import vendors from CSV. Bank details differing from the vendor master raise a `block` (fraud signal; see C3). | Proposed: PO to confirm |
| 6 | Retention? | Keep files and the audit trail for at least **8 years**. GST law requires 72 months from the due date of the annual return, which reaches up to about 7.7 years after an invoice. v1 has no deletion feature. | Proposed: finance controller to confirm |
| 7 | Login? | Local accounts in v1 (Argon2id), with the auth layer kept behind an interface so SSO (OIDC) can be added later as its own intent. | Proposed |
| 8 | Near-duplicate thresholds (FR-31)? | Warn only when invoice dates are within 30 days **and** either the numbers are 1 edit apart or totals are within 1%. Without the date window, consecutive numbers from regular suppliers (INV-41, INV-42) warned on almost every invoice (PR #26 review). | Decided by PO, 2026-11-23 |
| 9 | Can a person holding both admin and approver approve? | **No.** Holding `admin` on an entity blocks approve and reject there, whatever other roles the person has (§2 role table). | Decided by PO, 2026-11-23 (PR #25 review) |

---

## 2. Users, roles and permissions

Roles are granted **per entity**. A user can hold different roles in different entities.

| Role | Can |
|---|---|
| `clerk` | Upload; see the queue; correct extracted fields; resolve or override flags (only with `flags:override`); submit for approval |
| `approver` (with tier 1..n) | Approve or reject invoices at their tier |
| `accountant` | Export approved invoices; mark them paid |
| `admin` | Manage entities, registrations, tiers, users, inboxes and folders, vendor import; reopen invoices. Cannot approve (approval-and-audit §2) |
| `auditor` | Read-only on everything, including the audit trail; verify the hash chain |

Extra permissions: `flags:override`, `bank_details:view`.

---

## 3. Functional requirements

### Entities and configuration
- **FR-1** Admins create entities (legal name, PAN), each with one or more GST registrations (GSTIN, state, address).
  *AC:* a GSTIN failing the format or checksum rule cannot be saved; the same GSTIN cannot belong to two entities.
- **FR-2** Admins define approval tiers per entity (ordered amount bands, with approvers per tier).
  *AC:* bands are contiguous and non-overlapping; saving invalid bands fails with a message; changes are audited.
- **FR-3** Admins manage users and per-entity role grants.
  *AC:* a user with no grant in an entity sees nothing from it (entity-isolation test, data-protection §2).

### Intake
- **FR-10** Web upload: one or many files at once (drag and drop), assigned to an entity or left for auto-assignment.
  *AC:* files are validated by magic bytes, 20 MB and 50 pages at most; a rejection gives a reason per file.
- **FR-11** Email intake: per-entity or shared IMAP inboxes, polled every 2 minutes; each allowed attachment becomes an invoice. Message ID and sender are recorded.
  *AC:* the same message is never ingested twice; a message with no valid attachment is logged and skipped.
- **FR-12** Watched folders: polled every 30 seconds; files are moved to `processed/` or `rejected/` afterwards.
  *AC:* a half-written file is not picked up (size stable across 2 polls).
- **FR-13** Every intake creates an invoice in state `received`, with source metadata and a SHA-256 hash of the file. An identical hash already on file for the same entity raises an exact-duplicate `block` straight away.

### Extraction
- **FR-20** A background worker extracts each `received` invoice with the Claude API (§5.3) into the invoice schema: header fields, supplier and buyer blocks, line items, tax summary, bank details and IRN.
- **FR-21** Each extracted field has a confidence of `high`, `medium` or `low`, combining the model's own rating with deterministic checks (§5.3). Fields below the threshold are listed first on the review screen.
- **FR-22** Extraction failure (API error, refusal, schema-invalid output after 2 retries) sets `extraction_failed`, with the reason. The clerk can retry or enter the fields by hand.
- **FR-23** After extraction the invoice moves to `needs_review`, unless every field is `high` confidence with no `block` or `warn` flags. Then it goes straight to `ready_for_approval` (the touch-free path behind success measure 2).

### Validation (gst-invoice-rules)
- **FR-25** Validation runs after extraction and after every edit, producing flags with severity, message and rule ID. It implements every rule in gst-invoice-rules §1-§8.
  *AC:* each rule has a passing and a failing unit test (see the skill's Tests section).
- **FR-26** The tax layer is a `TaxRegime` interface (`validate(invoice) -> flags`, `rate_table`). `IndiaGST` is the only implementation in v1, chosen per entity.
- **FR-27** The rate table is data with effective-from dates, editable by admins and audited.

### Duplicates
- **FR-30** Exact duplicate: same supplier GSTIN + normalised invoice number + financial year, or the same file hash → `block`, linking to the original.
- **FR-31** Near-duplicate: same supplier, invoice dates within 30 days, and either normalised numbers within edit distance 1 or totals within 1% → `warn`, linking to the candidates. (Amended 2026-11-23, decision 8: the 30-day window now applies to both conditions.)
- **FR-32** Duplicate checks cover all invoices of **all entities** for the same supplier GSTIN, because the intent names cross-entity duplicates as a cost. A clerk sees a cross-entity match as "possible duplicate in another entity", without details of an entity they have no access to.
- **FR-33** Overriding a duplicate `block` follows approval-and-audit §3.

### Review
- **FR-35** The review screen shows the document (PDF or image viewer) beside the editable fields, low-confidence fields first, and flags with fix guidance (ux-standards).
- **FR-36** Every field edit re-runs validation and records an audit event with the old and new value.
- **FR-37** "Submit for approval" is enabled only when there are no open `block` flags.

### Approval
- **FR-40** On submission the invoice snapshots its entity's tier configuration and routes to tier 1. Each tier notifies its approvers (in-app list, plus email if SMTP is configured).
- **FR-41** An approver approves (acknowledging any `warn` flags) or rejects (reason required). A rejection returns the invoice to `needs_review`.
- **FR-42** Separation of duties is enforced on the server (approval-and-audit §2), with tests for each rule.
- **FR-43** When the last required tier approves, the invoice becomes `approved`.
- **FR-44** An admin can reopen approved, exported or paid invoices with a reason, which voids prior approvals.

### Export and payment
- **FR-50** Accountants export approved invoices per entity and date range as CSV and JSON in the documented `invoice-flow export v1` format, one row per invoice plus an optional line-item file. Bank details are masked unless the exporter holds `bank_details:view` and ticks "include bank details", which is audited.
- **FR-51** An export marks its invoices `exported` and records an export batch. Re-exporting a batch is allowed and audited.
- **FR-52** An accountant marks invoices `paid` with a payment date and reference.

### Vendors
- **FR-55** Vendor master per supplier GSTIN, created on first approval or by CSV import. Bank details are stored encrypted.
- **FR-56** An invoice whose bank details differ from the vendor master raises a `block` ("bank details changed"). Updating the vendor master's bank details requires an admin other than the clerk on the invoice, and is audited.

### Audit
- **FR-60** An audit event for every action listed in approval-and-audit §5, written in the same transaction, append-only, and hash-chained.
- **FR-61** The invoice page shows its history. Auditors can search events and run `verify-audit-chain`.

### Dashboard
- **FR-65** A queue view per user: my work (to review or to approve), counts by state, and age of the oldest item per state.

---

## 4. Non-functional requirements
- **NFR-1 Volume:** 2,000 invoices a month on one machine, with peaks of 200 uploaded together. Extraction runs concurrently (4 workers by default) and the queue drains a 200-invoice burst in under 30 minutes.
- **NFR-2 Latency:** UI actions respond in under 200 ms at the server (p95). Extraction of a 1-3 page invoice takes under 45 s (p90).
- **NFR-3 Security:** everything in data-protection, each rule backed by a test.
- **NFR-4 Reliability:** a crash or restart loses no invoice. Queue items in `extracting` for more than 10 minutes are retried.
- **NFR-5 Operability:** one command each to run, test and lint; structured JSON logs without restricted data; `/health` reports database, worker and intake status.
- **NFR-6 Portability:** runs on macOS and Linux with Python 3.13; SQLite file plus a files directory; no external services except the Claude API and optional IMAP/SMTP.

---

## 5. Design

### 5.1 Architecture
A single Python process (FastAPI + Uvicorn) with in-process background workers:

```
 Upload (HTTP) ─┐
 IMAP poller ───┼─> intake service ─> files/ + invoices(received) ─> job queue (DB table)
 Folder poller ─┘                                                    │
                                     extraction worker(s) <──────────┘
                                         │ Claude API
                                         v
                       validation (TaxRegime) + duplicate check -> flags
                                         v
                  review UI (HTMX) -> approval routing -> export / paid
                         every step -> audit_events (hash-chained, same transaction)
```

- **Stack:** FastAPI, Jinja2 + HTMX, SQLAlchemy 2 + Alembic on SQLite (WAL mode), Pydantic v2,
  `anthropic` Python SDK, `pypdf` (text layer and page count), Pillow, `argon2-cffi`,
  `cryptography` (Fernet field encryption, key from `INVOICE_FLOW_FIELD_KEY`).
- **Code layout:** `app/` with `api/` (routes), `domain/` (invoice, state machine, money),
  `tax/` (TaxRegime, IndiaGST, rate tables), `extraction/`, `intake/`, `approval/`,
  `audit/`, `export/`, `templates/`; `tests/` mirrors it; `evals/` holds the extraction eval.
- **Job queue:** a `jobs` table with claim-by-update (`status`, `locked_until`). No Redis,
  which is enough at NFR-1 volumes.

### 5.2 Invoice state machine

```
received -> extracting -> needs_review <-> (edits) 
                     \-> extraction_failed -> (retry) extracting | (manual) needs_review
needs_review -> ready_for_approval -> in_approval(tier k) -> approved -> exported -> paid
in_approval -> rejected -> needs_review
needs_review -> discarded (duplicate or not an invoice; reason required)
approved | exported | paid -> (admin reopen) needs_review
```
Transitions live in one module, with one function per transition that checks the actor's
permission and separation-of-duties rules and writes the audit event. Every other code path
goes through it.

### 5.3 Extraction design
- **Call:** Anthropic Python SDK, `client.messages.parse(...)` with a Pydantic
  `ExtractedInvoice` schema (structured outputs), model **`claude-opus-5-5`**,
  `output_config.effort` set explicitly (starting at `medium`, tuned by the eval), PDFs as a
  base64 `document` block and images as an `image` block. Server-side refusal fallback is
  enabled (`fallbacks: "default"`). The system prompt and schema are static and cached
  (prompt caching).
- **Prompt rules:** the document is untrusted data; extract only what is printed; return `null`
  rather than guess; report the confidence of each field and the page it was found on.
- **Confidence =** the model's rating, lowered one level when a deterministic check fails:
  the value is absent from the PDF text layer (when there is one), GSTIN checksum fails, the
  arithmetic does not reconcile, or the date is in the future or more than 18 months old.
- **Cost estimate:** about 5-10k input tokens and 1-3k output per invoice, so about $0.03-0.10
  each, or **$60-200 per month at 2,000 invoices**. The eval measures it; see C6.
- **Retries:** API 429 and 5xx retried by the SDK (`max_retries=3`). Schema-invalid output
  retried once, then `extraction_failed`.

### 5.4 Data model (main tables)
`entities`, `gst_registrations`, `users`, `role_grants(user, entity, role, tier)`,
`approval_tiers(entity, min, max, approvers)`, `vendors(gstin, name, bank_enc, …)`,
`invoices(entity, state, source, file_hash, supplier_gstin, number_norm, fy, totals…)`,
`invoice_fields(invoice, name, value, confidence, page)`, `line_items`, `flags(invoice,
rule_id, severity, status, override_reason, overridden_by)`, `approvals(invoice, tier,
approver, decision, reason, at)`, `export_batches`, `jobs`, `rate_tables`,
`audit_events(seq, at, actor, entity, invoice, action, data_json, prev_hash, hash)`.
Money is stored as integer paise; Python code uses `Decimal`.

### 5.5 Configuration
Environment variables: `ANTHROPIC_API_KEY`, `INVOICE_FLOW_FIELD_KEY`,
`INVOICE_FLOW_DATA_DIR`, `INVOICE_FLOW_SECRET_KEY` (sessions), optional
`IMAP_*` and `SMTP_*`. Inboxes and folders are configured in the admin UI; their passwords
come only from the environment, referenced by name.

---

## 6. Success measures: how each is verified

| Intent measure | Verification |
|---|---|
| 1. ≥95% field accuracy | `evals/extraction`: at least 60 synthetic invoices (varied layouts, digital PDFs, scanned-style images with noise and skew, multi-page, intra- and inter-state), with ground-truth JSON. Field accuracy is reported per field and overall over the 7 named fields, using exact match after normalisation (amounts to the paisa, dates as ISO). Run with `make eval`; nightly in CI and on any change to the extraction prompt or schema. Gate: ≥95% overall, no named field below 90%. |
| 2. Clean invoice to ready-for-approval in under 1 minute, no edits | Measured from `received` to `ready_for_approval` on the clean subset of the eval set (no seeded errors), via the FR-23 touch-free path. Gate: p90 under 60 s and ≥80% of clean invoices touch-free. Also logged in production as a metric. |
| 3. 100% of seeded duplicates flagged | The eval set includes at least 15 seeded duplicates (exact, re-scanned, renumbered `INV-0042`/`INV/42`, same amount and date, cross-entity). A test asserts that every one is flagged before approval. Gate: 100%. |

---

## 7. Areas of concern

Each concern needs a decision from its policy owner before engineering starts. The
product owner resolves them in the PR.

**C1. Real accuracy cannot be measured with synthetic invoices alone.** *(Product owner, security)*
The repo is public, so data-protection §7 forbids real invoices in it. But a 95% score on
synthetic invoices may overstate real-world accuracy.
*Options:* (a) synthetic only; (b) also keep a private, anonymised real-invoice set outside
the repo, run locally through the same `make eval` with a path argument.
*Recommendation:* (b). The public gate stays synthetic; the go-live decision uses the private set.

**C2. Duplicate checks across entities against entity isolation.** *(Security lead and finance controller)*
FR-32 checks duplicates across entities, while data-protection §2 isolates entities. These
policies pull against each other.
*Recommendation:* run the check across entities but show only "possible duplicate in another
entity" plus a reference an admin can look up. No details from the other entity leak.

**C3. Bank-detail changes as a fraud signal.** *(Finance controller)*
FR-56 blocks any invoice whose bank details differ from the vendor master. This stops the
most common invoice-fraud pattern, but vendors who genuinely changed banks will be blocked
until an admin verifies them out of band, for example by phone.
*Recommendation:* keep the `block`. Show an "update vendor bank details" action that needs a
second person.

**C4. Small entities may not have enough distinct approvers.** *(Finance controller)*
Distinct approvers per tier plus "the uploader can't approve" means a 3-tier invoice needs 4
different people. A small entity may not have them, and its invoices would stall.
*Options:* (a) keep the rule strict, so such entities configure fewer tiers; (b) allow an
entity-level exception, recorded and audited.
*Recommendation:* (a). Admin config validation warns when a tier has fewer than two eligible approvers.

**C5. The GST rate table must be correct and current.** *(Finance controller)*
The skill sets out the post-September-2025 slabs, but the seed table, including the HSN
categories at 40% and 3%, must be checked against current notifications by someone accountable.
*Recommendation:* ship the seed rates as data with a "last verified" date shown in admin, and
make an unknown rate a `warn`, never a `block`.

**C6. Model cost against accuracy.** *(Product owner)*
`claude-opus-5-5` gives the best chance of meeting 95%, at an estimated $60-200 per month. A
smaller model could be 10-40× cheaper but may miss the gate.
*Recommendation:* start on Opus 5.5. Once the eval exists, compare a smaller model and lower
effort on the same eval, then switch only if it holds the gate. That switch is your decision.

**C7. Email intake accepts mail from anyone.** *(Security lead)*
Any sender can put files in the queue: spam, malware-bearing PDFs, or fake invoices.
*Recommendation:* in v1 accept attachments only from senders whose domain matches a known
vendor or an allow-list per inbox; quarantine everything else for a clerk to release. Files
are never opened by anything except `pypdf`, Pillow and the Claude API.

**C8. Touch-free path (FR-23) against "a person decides".** *(Product owner and finance controller)*
Skipping review for all-high-confidence, flag-free invoices is how success measure 2 is met.
A human still approves, so the review step, not the control, is skipped.
*Recommendation:* allow it, per entity, with a setting that defaults to on, and show
"auto-reviewed" to approvers.

---

## 8. Out of scope (from intent) and deferred items
Accounting-system integration, payment files, PO and goods-receipt matching, other tax
regimes (interface only), a vendor portal, mobile apps, SSO, multi-currency conversion,
deletion and retention jobs.
