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
| S7 | Upload intake, file store, magic-byte checks, file hash, jobs table + worker | FR-10, FR-13, NFR-4 | S5 | yes |
| S8 | Vendor master, field encryption, bank masking and audited unmask, bank-change `block` | FR-55–56 | S5 | yes |
| S9 | Claude extraction: schema, prompt, confidence merge, retries, touch-free path | FR-20–23 | S7, S2 | yes |
| S10 | Extraction eval: `make eval` (accuracy, touch time, duplicate catch), with a private-set path option (C1) | §6, C1, C6 | S9, S4, S3 | no |
| S11 | Review screen: document viewer, fields, flags, edits re-validate, submit; duplicate flags wired in, cross-entity safe (C2) | FR-32–37 | S9, S3, S8 | yes |
| S12 | Approval workflow: routing snapshot, approve/reject, SoD rules, reopen, in-app notifications, under-staffed tier warning (C4) | FR-40–44 | S11, S6 | yes |
| S13 | Export (CSV/JSON v1) and mark paid | FR-50–52 | S12 | yes |
| S14 | Email (IMAP) and watched-folder intake, sender allow-list + quarantine (C7) | FR-11–12 | S7 | yes |
| S15 | Queue dashboard, invoice history, audit search | FR-61, FR-65 | S12 | no |

**Waves (parallelism):**
1. S1, S2, S3, S4 (only S1 touches the DB, so all four run in parallel)
2. S5, then S6, S7, S8 one after another (DB)
3. S9, then S10 and S11
4. S12, then S13, S14, S15

Each issue uses the ticket template: what, links (intent, FR IDs, plan §), acceptance
criteria copied from the spec, applicable skills, and "Done = PR with `make test`/`make lint`
output". Concern decisions are carried into the matching issues (C2→S11, C3→S8, C4→S12,
C5→S2, C6→S10, C7→S14, C8→S9).

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
