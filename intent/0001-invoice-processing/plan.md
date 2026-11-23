# Plan 0001: invoice-flow build (from spec.md at 1f11661)
Accepted by: Sandeep Khokher (engineer) in Claude Code plan mode · Date: 2026-11-20
Produced in plan mode (read-only) from intent.md and spec.md; approved via plan-mode exit.

## Context
Stage 3 (Build) of the AI-native SDLC for invoice-flow. Intent 0001 (PR #1) and spec 0001
(PR #3) are merged. Merging PR #3 without comments is recorded as **accepting all eight
concern recommendations C1–C8 and the four policy skills as written**. The repo has no code
yet. This plan says how the spec becomes code: which slices, in what order, who builds
them (Claude interactively vs the ticket runner), what guardrails come first, and how each
slice is proven. Once you accept it, it is committed as `intent/0001-invoice-processing/plan.md`,
and each slice becomes a GitHub Issue.

## Approach in one paragraph
Claude builds the **platform** by hand first, as reviewed PRs: the scaffold with one-command
test, lint and run; CLAUDE.md; hooks; the verifier subagent; and the ticket runner. Then
every **product** slice is a GitHub Issue that the local runner builds headless in its own
worktree and turns into a PR. Pure-library slices run in parallel. Slices that touch the
database run one after another, to avoid conflicting migrations.

## Phase A: platform (Claude, interactive, one PR each)

**A1. Scaffold and feedback loop (S0)**
- `pyproject.toml` (uv, Python 3.13): fastapi, uvicorn, jinja2, sqlalchemy 2, alembic,
  pydantic 2, anthropic, pypdf, pillow, argon2-cffi, cryptography, python-multipart; dev:
  pytest, pytest-cov, ruff, httpx.
- `app/main.py` (FastAPI factory, `/health`), `app/config.py` (env settings),
  `app/db.py` (SQLite WAL, session), `alembic/` (empty baseline), `templates/base.html`
  (HTMX from a pinned CDN version).
- `Makefile`: `make setup | run | test | lint | fmt | eval | verify-audit`. `make test` =
  `uv run pytest -q`, `make lint` = `ruff check && ruff format --check`.
- `.github/workflows/ci.yml`: lint + test on every PR (no API key needed).
- Proof: `make test` passes (health test), `make lint` clean, CI green on the PR.

**A2. CLAUDE.md**: commands with healthy output, conventions (money as paise/`Decimal`;
every state change goes through `app/domain/transitions.py`; queries through the
entity-scoped repository; never touch `intent/**` or `.claude/skills/**`; skills to apply
per area), architecture map, a "things Claude gets wrong" section (empty to start), and
the verification block: "run make test and make lint before reporting done; paste output;
fix code, not tests".

**A3. Guardrails** (`.claude/settings.json` + `.claude/hooks/`)
- PreToolUse on Edit/Write: block `intent/**/intent.md`, `intent/**/spec.md` and
  `.claude/skills/**` (approved artifacts and policies; changing them needs a human PR);
  block content matching secret patterns (`sk-ant-`, `-----BEGIN .* PRIVATE KEY`,
  `AKIA…`). The block message says how to get approval.
- PreToolUse on Bash/Read: deny reading `.env*`.
- PostToolUse on Edit/Write of `*.py`: `ruff format` + `ruff check --fix` on that file only (fast).
- Permissions allow-list: `make test|lint|fmt`, `uv run *`, `git *` (no push to main),
  `gh issue view *`, `gh pr create *`.
- Proof: a pytest that feeds sample hook inputs to each hook script and checks exit codes 0/2.

**A4. Verifier subagent** `.claude/agents/verifier.md` (tools: Bash, Read): runs
`make test lint`, starts the app, checks the ticket's acceptance criteria and reports
mismatches against plan.md. Reports only; never fixes.

**A5. Ticket runner** `scripts/ticket_runner.py` (plain Python, deterministic plumbing):
1. `gh issue list --label agent:ready`, oldest first, at most 2 at a time.
2. Claim: swap the label to `agent:working`, comment "picked up".
3. `git worktree add .worktrees/issue-N -b ticket/N-slug origin/main`.
4. `claude -p "<.claude/runner-prompt.md filled with issue N>" --permission-mode auto
   --output-format json` with cwd set to the worktree. The prompt: read the issue, the
   intent, spec and plan, apply skills, implement, run tests, use the verifier, and commit.
5. **Independent gate:** the runner itself re-runs `make test` and `make lint` in the worktree.
6. Pass → push the branch, `gh pr create` with "Closes #N", the test output and the session
   cost, label `agent:pr-open`. Fail → label `agent:blocked`, comment the tail of the output.
- `make runner` runs one polling pass; `make runner-loop` polls every 2 minutes.
- Labels: `agent:ready`, `agent:working`, `agent:pr-open`, `agent:blocked`.
- Proof: a dry run on the first product ticket; the PR appears and links back to the issue.

## Phase B: product slices (GitHub Issues → runner)

| ID | Slice | Spec refs | Depends | Touches DB |
|---|---|---|---|---|
| S1 | Domain core: money, invoice state machine and transitions module, audit events with hash chain + `make verify-audit` | 5.2, FR-60, NFR-4 | A1 | yes |
| S2 | GST library: `TaxRegime` interface, `IndiaGST` (GSTIN checksum, heads and place of supply, rate table with effective dates, arithmetic, HSN, IRN) | FR-25–27 | A1 | no |
| S3 | Duplicate library: number normalisation, FY, exact and near rules | FR-30–31 | A1 | no |
| S4 | Synthetic invoice generator: about 60 PDFs/images with ground-truth JSON, 15+ seeded duplicates, intra and inter-state, scanned-style noise | §6 | A1 | no |
| S5 | Auth, users, entities, GST registrations, role grants, entity-scoped repository, login page | FR-1, FR-3, DP §2–3 | S1 | yes |
| S6 | Approval tier config + admin UI | FR-2 | S5 | yes |
| S7 | Upload intake, file store, magic-byte checks, file hash, jobs worker (table built in S1) | FR-10, FR-13, NFR-4 | S5 | yes |
| S8 | Vendor master, field encryption, bank masking and audited unmask, bank-change `block` | FR-55–56 | S5 | yes |
| S9 | Claude extraction: schema, prompt, confidence merge, retries, touch-free path | FR-20–23 | S7, S2 | yes |
| S10 | Extraction eval: `make eval` (accuracy, touch time, duplicate catch), with a private-set path option (C1) | §6, C1, C6 | S9, S4, S3 | no |
| S11 | Review screen: document viewer, fields, flags, edits re-validate, submit; duplicate flags wired in, cross-entity safe (C2) | FR-32–37 | S9, S3, S8 | yes |
| S12 | Approval workflow: routing snapshot, approve/reject, SoD rules, reopen, in-app notifications, under-staffed tier warning (C4) | FR-40–44 | S11, S6 | yes |
| S13 | Export (CSV/JSON v1) and mark paid | FR-50–52 | S12 | yes |
| S14 | Email (IMAP) and watched-folder intake, sender allow-list + quarantine (C7) | FR-11–12 | S7 | yes |
| S15 | Queue dashboard, invoice history, audit search | FR-61, FR-65 | S12 | no |

**Waves (parallelism), as first planned:**
1. S1, S2, S3, S4 (only S1 touches the DB, so all four run in parallel)
2. S5, then S6, S7, S8 one after another (DB)
3. S9, then S10 and S11
4. S12, then S13, S14, S15

**Re-sequenced 2026-11-23: walking skeleton first.** After wave 1 and S5 the product owner
could click only login and two admin pages; the end-to-end workflow would have appeared only
in wave 4. The bottom-up order was sound engineering but gave no visible product for most of
the build. From here the order puts one thin path through the whole journey first:

| Step | Slice | Thin version (what is deferred) |
|---|---|---|
| 1 | S5.1 (#36) | Apply the visual design (running) |
| 2 | S7 (#12) | Upload + invoice list, as planned |
| 3 | S9 (#14) | Extraction; without `ANTHROPIC_API_KEY` a demo extractor returns the ground truth for the synthetic invoices, so the flow works offline |
| 4 | S11 (#16) | Review screen; bank-detail checks deferred to S8 |
| 5 | S12 (#17) | Approval; with no tier configuration an entity gets one default tier of all its approvers (S6 adds configuration) |
| 6 | S13 (#18) | Export and mark paid; bank-detail columns deferred to S8 |

Proof of the skeleton: upload a synthetic invoice in the browser and take it to `paid`.
Then, deepening: S6 tiers, S8 vendors and bank details, S5.2 auth hardening (#38),
S10 eval, S14 email and folder intake, S15 dashboard, and the review follow-ups #29–#32.
All skeleton steps touch the DB, so they run one after another. Review depth: CI, the
runner's gate and the verifier on every PR; a full independent review for S9 (model output
handling) and S12 (separation of duties); a quick read for the rest.

Each issue uses the ticket template: what, links (intent, FR IDs, plan §), acceptance
criteria copied from the spec, applicable skills, and "Done = PR with `make test`/`make lint`
output". Concern decisions are carried into the matching issues (C2→S11, C3→S8, C4→S12,
C5→S2, C6→S10, C7→S14, C8→S9).

**Screens (added 2026-11-23).** `screens.md` lists every screen (SC-1 to SC-50), who sees it, its
layout, and which slice builds it (screens.md §5). UI slices S5–S15 build to it. GST rate-table
editing (SC-45) moves to S6, as S2 proposed.

**Visual design (added 2026-11-23).** `visual-design.md` sets the look; its stylesheet and style
guide live in `doc/design/`. Ticket S5.1 moves the stylesheet and self-hosted fonts into
`app/static/` and restyles whatever S5 built; later UI slices use it from the start.

## Risks (interrogated)
- **Riskiest step: S9/S10, extraction accuracy.** Synthetic invoices may be too easy (C1).
  Mitigation: build the generator (S4) in wave 1 with realistic variety; run the eval before
  building the UI on top; a private real-invoice set is decided at go-live.
- **Parallel agents colliding.** Two slices adding Alembic migrations create multiple heads.
  Mitigation: DB slices run one after another; pure libraries run in parallel; the runner
  starts every slice from fresh `origin/main`.
- **Auto mode doing something unsafe.** Hooks block policy and artifact edits and secrets;
  the runner's own test gate stops failing work becoming a PR; branch protection on `main`
  (set up in Stage 5) means a human merges.
- **Audit integrity.** "Audit in the same transaction" fails if any code bypasses the
  transitions module. Mitigation: CLAUDE.md rule + a test that every state change produces an event.
- **SQLite with worker threads.** WAL mode, short transactions, retry on `database is locked`.
- **Cost and quota.** The runner uses your Claude login, limited to 2 concurrent sessions.
  Extraction tests mock the API; only `make eval` calls Claude (needs `ANTHROPIC_API_KEY`
  or an `ant auth login` profile).

## Alternatives considered and rejected
- Postgres + Celery/Redis: not needed at 2,000 invoices a month (NFR-1); SQLite + jobs table is enough.
- React SPA: more moving parts; HTMX meets ux-standards and keeps one language.
- Building tickets with claude-code-action in CI: needs an API key and costs; local first,
  as you chose. CI review comes in Stage 5.
- `claude --worktree` inside the runner: the runner manages worktrees with git directly
  so branch names and cleanup are deterministic.
- One big agent session for the whole build: no reviewable units, no parallelism, and no
  per-story audit trail.

## Verification (end to end)
- Every PR: CI lint + test green; the runner's PRs include test output.
- After S9: `make eval` reports field accuracy, touch-free rate and p90 time, and duplicate catch.
- After S13: a browser walkthrough (Chrome tools, with screenshots) of upload synthetic invoice →
  extracted → review → approve (tiers, separation of duties) → export CSV; then
  `make verify-audit` passes.

## Files committed right after you accept this plan
- `intent/0001-invoice-processing/plan.md` (this plan, "Accepted by" filled in)
- 15 GitHub Issues S1–S15 (labelled `stage:build`; wave-1 issues also `agent:ready` once A5 is merged)
- Then the Phase A PRs, in order A1 → A5.

## Departures from this plan (recorded as built)
Phase A was built as PRs #21 (A1) and #22 (A2–A5). Differences from the plan above:
- **A3 guardrails protect themselves.** The edit hook also blocks `.claude/hooks/**` and
  `.claude/settings.json`; otherwise an agent could switch off its own guardrails. The Bash hook
  also blocks shell writes (`>`, `tee`, `sed -i`, …) to every protected file. A live headless
  test found that gap, and it was fixed in #22.
- **A3 blocks more git and gh actions.** Force push, deleting remote branches, `gh pr merge` and
  `gh repo delete` are blocked as well as pushing to main. Merging is the human gate.
- **A3 allow-list.** It does not include `gh pr create`: the runner opens PRs, not the session.
- **A5 builds only trusted tickets.** The repo is public and issue bodies become prompts, so the
  runner builds only issues written by the repo owner.
- **A5 needs the workspace trusted.** Headless sessions ignore `permissions.allow` in an untrusted
  folder. Run `claude` interactively in the repo once and accept the trust dialog.

### S1 departures
S1 (domain core, issue #6) differs from the plan as follows:
- **Jobs table moved into S1.** Issue #6 asks for the `jobs` table in S1's migration, so S1 creates
  the table (`app/jobs.py`) and S7 builds only the worker that claims and runs jobs.
- **No entities table yet.** `invoices.entity_id` and `audit_events.entity_id` are plain indexed
  integers. S5 adds `entities` and the foreign key from `invoices`.
- **S1 enforces roles; S12 adds separation of duties.** `transitions.py` checks the actor's role on
  the invoice's entity (system-only steps refuse users, user steps refuse the system, admins cannot
  approve) and requires a reason for discard, reject, reopen and extraction failure. The §2 rules
  that need approval and edit history (uploader/editor cannot approve, one tier per person) are
  added to the same functions in S12. The "no open `block` flags" check on submit (FR-37) comes
  with flags in S11. Tier-by-tier progress inside `in_approval` is S12; it is not a state change.
- **FR-23 touch-free edge.** The state machine includes `extracting -> ready_for_approval`
  (`extraction_touch_free`), which FR-23 needs but the §5.2 diagram does not draw.
- **Creation is audited.** `transitions.create_invoice` creates every invoice in `received` and
  writes `invoice.received`; intake slices (S7, S14) call it.
- **Append-only at two levels.** ORM listeners refuse updates and deletes of audit events, and the
  migration adds SQLite triggers that refuse them for raw SQL too.
- **`alembic/env.py`** imports `app.models`, accepts a caller-supplied URL (tests migrate temporary
  databases with the real migrations), uses batch mode for SQLite, and keeps existing loggers.
- **`make migrate`** was added (`alembic upgrade head`), since `make verify-audit` needs a migrated
  database. `make verify-audit` exits 0 when the chain is intact, 1 when broken, 2 when it cannot
  verify (database missing or not migrated).

### S2 departures
S2 (GST library, #7) was built in `app/tax/`. Differences from the plan above:
- **Rule scope is §1-§7.** §8 (duplicates) is in S3's library, as the S3 row already says; FR-25's
  "§1-§8" is met by S2 and S3 together.
- **Rate table is read-only data in S2.** `app/tax/data/india_gst_rates.json` has effective-from
  slab sets and `last_verified`. FR-27's "editable by admins and audited" needs the DB and the admin
  UI, and no slice in the table owns it yet. Proposed: add it to S6 (admin config UI).
- **C5 is still open.** The seed rates come from gst-invoice-rules §4 only, and `verified_by` says
  they are UNVERIFIED. `last_verified` is `null` until the finance controller checks them against
  current notifications and sets the date, before go-live.
- **Interface details.** `IndiaGST(entity_gstins, rate_table=None)`, because the "buyer GSTIN is ours"
  check needs the entity's registrations. `validate(invoice, *, vendor_e_invoice_mandated=False)`
  takes the §7 vendor-master flag as a keyword, because the vendor master arrives in S8. The tax
  layer's `Invoice` is its own frozen dataclass; S9 maps extraction output onto it.
- **Not checked by S2.** The §2 reverse-charge indicator is "where applicable", and the invoice alone
  cannot say when it applies, so it is not flagged. §7 "record the IRN": `Invoice.irn` carries it;
  storing it belongs to S9.
- **The state code of an invalid supplier GSTIN is still used** to decide intra- or inter-state, so
  a wrong check character does not hide a wrong tax head. Both flags are `block`.

### S4 departures
S4 (synthetic invoice generator, #9) differences:
- **The generated set is not committed.** `make gen-invoices` writes `evals/data/` (about 19 MB),
  which `.gitignore` ignores (`evals/data/`, added explicitly when S2 anchored `/data/`). The set is byte-identical for
  the fixed seed (a test proves it), so S10's `make eval` should run `make gen-invoices` first.
- **Two dev dependencies:** `fpdf2` (digital PDFs with a real text layer) and `segno` (QR codes).
  They are dev-only; the app does not import them.
- **The generator lives in `evals/synth/`** and has its own small GSTIN checksum and number
  normaliser, so it does not wait for S2 and S3 (parallel-safe). S10 should check S2/S3 against
  the ground truth rather than reuse these.
- **Synthetic markers:** every PAN starts with `ZX`, bank IFSC codes use fictitious `ZY..` bank
  codes, emails use `.example`, and every page carries a small "Synthetic sample" footer.
- **Rule IDs in the manifest are provisional** (`gst.tax_head`, `gst.gstin_checksum`,
  `gst.arithmetic`, `duplicate.exact`, `duplicate.near`); S2/S3 may name them differently, and
  S10 maps them.

### S5 departures
S5 (auth, users, entities, GST registrations, grants, entity scoping, issue #10) differs as follows:
- **Server-side sessions.** Sessions live in a `user_sessions` table. The cookie holds a random
  token (HttpOnly, SameSite=Lax, Secure over HTTPS) and the table keeps only its SHA-256. With
  sessions stored server-side, logout, deactivation and password changes end sessions at once, and
  the 30-minute idle timeout is checked on the server. `INVOICE_FLOW_SECRET_KEY` is not used yet.
- **Extra permissions have their own table.** `flags:override` and `bank_details:view` are rows in
  `permission_grants(user, entity, permission)`, beside `role_grants(user, entity, role, tier)`
  from spec §5.4. A permission without a role on that entity is refused, and it gives no
  visibility. The domain `Actor` gained `permissions_by_entity` and `approver_tier_by_entity`.
- **One place for sign-in and CSRF.** A global FastAPI dependency (`app/api/deps.py: guard`) protects
  every route except `/login` and `/health`; a test walks the route table. CSRF uses the session's
  token (form field or `X-CSRF-Token` header for HTMX); `/login` uses a double-submit cookie.
  The OpenAPI/Swagger pages are switched off, since they would be routes without sign-in.
- **Entity scoping** is `app/scoping.py: EntityScope`. Lists follow the header's entity
  switcher, and lookups by id cover all of the user's entities. Out of scope gives `NotFound`
  (404). A section with no matching role gives `Forbidden` (403). Admin pages list every entity
  the admin manages, whatever the switcher says.
- **The app migrates on start** (`INVOICE_FLOW_AUTO_MIGRATE`, default on), so `make run` and the
  verifier work on an empty data directory. `make migrate` still exists.
- **The migration rebuilds `invoices`** (Alembic batch) to add the FK to `entities` that S1
  deferred. S1 tests that create invoices now use an `entity_rows` fixture; no assertion changed.
- **Admin rules not spelled out in the spec:** any admin may create an entity and becomes its
  admin. Entities are not deleted, because invoices and the audit trail reference them (spec §1 Q6).
  Registrations can be removed. A GSTIN must contain the entity's PAN (characters 3-12). An admin
  sees users with a grant on an entity they manage, plus users with no grant at all. They can
  deactivate a user only if they administer every entity that user can reach. An entity that has
  an admin must keep one.
- **Inviting a user** creates a random temporary password and shows it once to the admin (no SMTP
  in S5). It is not forced to change on first sign-in.
- **Lockout** after 5 failed sign-ins in a row, for 15 minutes, with the same generic message.
  Logins, failed logins, logouts and every entity, registration, grant and password change are
  audit events.
- **Menu items appear only once their page exists.** The role-to-menu mapping follows screens.md §2,
  but a link shows only when its route is registered, so S6-S15 add pages without editing the
  menu.
- **`make seed-demo`** creates two synthetic entities (PANs start `ZX`) with registrations in three
  states, and one user per role with that role on both entities. Each run sets random passwords
  (or `INVOICE_FLOW_DEMO_PASSWORD`) and prints them.
- **Not built:** there is no command to create the first admin on a production database;
  `make seed-demo` covers development. A follow-up ticket should add one.

### S5.1 departures
S5.1 (visual design applied to the shell and S5 screens, issue #36) differs as follows:
- **`/static` is served without sign-in.** The login page needs the stylesheet, fonts and HTMX
  before anyone signs in, so `create_app` mounts `app/static/` (Starlette `StaticFiles`, no
  directory listings). This is the one exception to "every route except `/login` and `/health`
  requires sign-in" (data-protection §3); the S5 test that forbade mounts now allows exactly this
  mount, and `tests/test_visual_design.py` limits the directory to `.css`, `.woff2`, `.js` and `.txt`
  files and checks that path traversal gets 404. **Security lead to confirm**, and to add the
  exception to data-protection §3 if accepted.
- **Stylesheet additions.** The S5 screens needed components the design did not have yet:
  skip link, entity switcher, account menu, nested side menu and menu button, `.panel` page
  sections, `.form-actions`, `.narrow`, role-grant groups (`.grants`, `.checks`), `.notice`
  (info, ok, error; icon + word), user `.status`, inline `.confirm`, the sign-in page, My work
  rows without counts (`.worklist .task`), the empty live region, and htmx's indicator rules.
  They are at the end of `invoice-flow.css` and in the style guide. The remaining raw hex and
  rgba values outside `:root` became tokens (`--carbon-deep`, `--carbon-wash`, `--zebra`,
  `--desk`, `--page-shadow`). Two fixes to the approved CSS: below 768 px the menu row no longer
  stretches to fill the screen (`grid-template-rows`), and the entity switcher wraps instead of
  overflowing.
- **Menu below 768 px.** screens.md §1.1 says the menu collapses into a menu button;
  visual-design.md says it becomes a scrolling row. Both: the menu button opens a scrolling row.
  Its toggle moved from an inline `onclick` to `app/static/js/shell.js`.
- **One primary action per screen.** The page-head "Add entity", "Add registration" and
  "Invite user" buttons duplicated the forms' submit buttons; they are now plain links to the
  form ("Add an entity below"), so each screen has one `.btn-primary`. My work has none until
  S15 adds "open the oldest task".
- **Fonts and HTMX are copied from upstream:** the `fonts/webfonts/` WOFF2 files (400, 700) and
  `OFL.txt` of googlefonts/atkinson-hyperlegible-next and -next-mono, and htmx 2.0.4 from
  unpkg, checked against its published SRI hash, which `base.html` also carries. htmx's own
  injected indicator `<style>` is switched off (`includeIndicatorStyles: false`).
- **Formatting helpers** for later slices: `money` filter (₹ and Indian grouping for INR; other
  currencies by ISO code; refuses `float`), `stamp(state)` and `gstin(value)` macros in
  `_macros.html`. S5 screens show no money or invoice states yet.
- **The style guide** links `../../app/static/css/invoice-flow.css` and no longer calls Google
  Fonts. Screenshots are in `doc/design/screenshots/s5.1/`.

### S7 departures
S7 (upload intake, file store, jobs worker, issue #12) differs as follows:
- **A `flags` table arrives in S7, not S11.** FR-13 needs the exact-duplicate `block` "straight
  away", so migration 0003 creates `flags` with the spec §5.4 columns (`app/domain/flags.py`) plus
  `entity_id` (for entity scoping), `message` and `related_invoice_id`. `raise_flag` writes the
  flag and a `flag.raised` audit event in one transaction. The rule ID is `DUP-EXACT-FILE` from
  S3's library. Only same-entity duplicates are flagged here; cross-entity matches (C2) stay with
  S11. A duplicate is still created in `received` and queued; the clerk discards or overrides it
  in review (S11).
- **Invoice columns for the source file:** `file_name` (display only), `stored_name` (generated,
  unique), `file_type`, `file_size`, `page_count`, `uploaded_by`. `uploaded_by` is a user id
  without a foreign key, because the S5 migration test pins the invoices foreign keys to the
  entity one; `invoice.received` also records the uploader (for S12's "uploader cannot approve").
  `transitions.create_invoice` takes an optional `data` dict, so the audit event carries the
  file's SHA-256, type, size and page count.
- **Auto-assignment, thin version.** A clerk of one entity gets that entity. A clerk of several
  gets the entity whose registered GSTIN appears in the PDF's text layer (first two pages),
  among the entities where they are a clerk; scans and ambiguous files are rejected with "Choose
  the entity and upload it again". Assignment from the extracted buyer GSTIN is not built; S9 may
  add it for scans.
- **Worker.** `app/worker.py` runs `INVOICE_FLOW_WORKER_THREADS` (default 4; 0 switches it off)
  threads from the app's lifespan, so it starts with `make run` and stops with the server.
  Handlers register by job kind in `app.state.job_handlers`; S7 registers none, so `extract` jobs
  stay queued and invoices stay `received` until S9 adds its handler. A handler's writes commit
  with the job's completion or roll back with it. `attempts` is a fencing token: a worker whose
  job was re-claimed after its 10-minute lock cannot finish it, and its writes roll back. Failed
  jobs are retried with a back-off of 30 s times the attempt, up to 3 attempts, then `failed`.
  Handlers must do slow work (the API call) before their first write, since SQLite has one writer.
- **`/health`** now returns `{"status","database","worker"}`; `worker` is `running`, `degraded`,
  `stopped` or `disabled`, and `status` is `ok` only when the database is up and the worker is
  running or disabled. The two S1/S5 tests that pinned the old body now expect the new one, and the
  S5 menu test now expects Invoices and Upload. Intake status (NFR-5) comes with S14.
- **Limits and files.** Settings `max_upload_bytes` (20 MB), `max_pages` (50) and
  `max_files_per_upload` (200, the NFR-1 burst). Files go to `INVOICE_FLOW_DATA_DIR/files/` as
  `<32 hex>.<ext>`, written to a temporary file, fsynced and renamed, mode 0600; `FileStore`
  refuses a root inside `app/static`. Nothing serves the files yet (S11's viewer will). Damaged
  PDFs and images are rejected too, and a TIFF's frames count as pages.
- **SC-3 basic list** shows the newest 200 invoices. Until extraction (S9) fills supplier, number
  and total, the file name stands in for the supplier; the invoice date column shows a dash
  because `invoices` has no date column yet (S9). Rows do not link anywhere until SC-4 (S11).
- **Stylesheet additions:** `.dropzone`, `.filelist`, `.filename`, `.result` (`-ok`, `-rejected`),
  `.flagmark` (`-block`, `-warn`) and `.ledger td.age`, also in the style guide. The drop zone works
  without JavaScript (the file input covers it); `app/static/js/upload.js` only lists the chosen
  files and highlights the zone. Screenshots are in `doc/design/screenshots/s7/`.

### S9 departures
S9 (Claude extraction worker, issue #14) differs as follows:
- **Package `app/extraction/`:** `schema.py` (`ExtractedInvoice`), `prompt.py` (static system
  prompt), `extractor.py` (`Extractor` interface, `ClaudeExtractor`, `DemoExtractor`,
  `make_extractor`), `checks.py` (deterministic checks, confidence merge, mapping onto S2's
  `Invoice`), `models.py` (`invoice_fields`) and `service.py` (the `extract` job handler,
  registered in `create_app`; `app.state.extraction` holds the extractor so tests swap it).
- **The API call** is `client.beta.messages.parse`: `fallbacks: "default"` needs the beta
  namespace and the `server-side-fallback-2026-07-01` beta. `output_format=ExtractedInvoice`,
  `output_config={"effort": "medium"}` (setting `INVOICE_FLOW_EXTRACTION_EFFORT`), thinking left at
  the model default (adaptive). The system prompt carries `cache_control`, but at about 700
  tokens it may be under the model's minimum cacheable prefix, so the cache may not engage; S10
  should read `cache_read_input_tokens` from the logs and decide whether it matters.
- **Schema-invalid output is retried once** (spec §5.3 and the ticket); FR-22's "after 2
  retries" reads as the same rule counted differently. A refusal (after the server-side
  fallback) fails at once. API errors are retried by the SDK (`max_retries=3`), then fail with a
  readable reason; extraction failures do not use the job queue's own retries.
- **Amounts and dates are strings in the schema** (plain decimal, ISO date), parsed with
  `Decimal`; an unparsable value is stored as printed with confidence `low`.
- **Confidence merge.** The text-layer check covers the header, party GSTINs, supplier name,
  totals and IRN, not line quantities and rates (they print in too many forms; the arithmetic
  check covers them). A PDF with under 40 characters of extractable text counts as a scan, and
  images skip the check. Several failed checks still lower a field one level only.
  The arithmetic check reuses S2's §5 flags.
- **Line items live in `invoice_fields`** under indexed names (`lines[0].hsn_sac`), not in a
  separate `line_items` table (spec §5.4), so the review screen (S11) edits every field the same
  way. `invoice_fields` keeps the model's rating, the merged confidence and the failed checks.
- **Bank details are stored masked only** (`XXXXXXXX7890`): the account number and IFSC are
  restricted, and field encryption is S8's. S8 should store the full values encrypted (a
  re-extraction or S8's vendor flow can supply them).
- **Invoice columns added:** `supplier_name`, `invoice_date`, `irn`, `auto_reviewed`; the
  existing `supplier_gstin`, `invoice_number`, `number_norm`, `fy` and money columns are filled.
  **`entities.touch_free`** (default on, C8) is the per-entity setting; it has no admin UI yet
  (S6's entity configuration can add the toggle).
- **Touch-free (FR-23):** every stored field `high` (null fields included, rated by the model as
  surely absent) and no open `block` or `warn` flag, including intake's exact-file duplicate.
  `info` flags do not stop it. The `extraction_touch_free` audit event carries
  `auto_reviewed: true`; SC-3 shows "Auto-reviewed" under the state, and approvers' screens (S12)
  should show the same.
- **Duplicate checks run within the invoice's own entity** (through `EntityScope`), against
  every non-discarded invoice with the same supplier GSTIN. Cross-entity matches (FR-32, C2) stay
  with S11. Identical files are not flagged again (intake's `DUP-EXACT-FILE` already is).
- **`received -> extracting` commits before the API call**, so SC-3 shows `Extracting` at once;
  the job's final writes commit with the job. A re-run (crash, or a clerk's retry in S11) picks
  up an invoice left `extracting` and replaces its fields; identical open flags are not raised
  twice.
- **SC-3** now shows supplier name and GSTIN, invoice date, the extraction-failure reason and
  "Auto-reviewed", and refreshes itself every 3 seconds (htmx) while any invoice is `received` or
  `extracting`. No stylesheet changes. Screenshots (demo extractor, synthetic invoices) are in
  `doc/design/screenshots/s9/`.
- **`make seed-demo` also registers the eval set's three buyer entities** (from
  `evals/data/manifest.json` when `make gen-invoices` has run), with the demo users' roles on
  them, so demo-extracted invoices are addressed to our registrations and can go touch-free.
  Not built: assigning an entity from the extracted buyer GSTIN (S7's note); scans still need the
  clerk to pick the entity when they have several.
- **Tests** never see a key: `tests/conftest.py` removes `ANTHROPIC_API_KEY` and points the demo
  manifest at an empty path, so the suite is the same on every machine.
