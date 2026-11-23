# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

invoice-flow: accounts-payable invoice processing for multi-entity companies (FastAPI, SQLite, HTMX).
Built through the AI-native SDLC: every change traces to `intent/NNNN-*/{intent,spec,plan}.md`.

## Commands
- Setup: `make setup` (uv sync)
- Test: `make test`. Healthy: `N passed`, no failures or errors
- Lint: `make lint`. Healthy: `All checks passed!` and `N files already formatted`
- Format and fix: `make fmt`
- Migrate: `make migrate` (Alembic upgrade head on the DB under `INVOICE_FLOW_DATA_DIR`)
- Verify audit chain: `make verify-audit`. Healthy: `audit chain OK: N events verified`
- Run: `make run`. Healthy: `GET http://127.0.0.1:8000/health` → `{"status":"ok","database":"ok"}`
- One test: `uv run pytest tests/test_health.py::test_health_reports_database_ok`
- Ticket runner: `make runner` (one pass over issues labelled `agent:ready`)

## Verifying your work
Run `make lint` and `make test` before reporting any task complete, and paste the output.
If a test fails, fix the code, not the test. Never skip, delete or weaken a test to make it pass.
For a bug fix, write the failing test first.

## Where the requirements are
- `intent/0001-invoice-processing/spec.md`: requirements (FR-n, NFR-n) and design; cite FR IDs in PRs.
- `intent/0001-invoice-processing/plan.md`: slices S1–S15, dependencies, risks.
- `intent/0001-invoice-processing/screens.md`: screen map (SC-n), roles per screen, layouts.
  Any ticket that adds a page builds the screens its slice lists there; cite SC IDs in PRs.
- `intent/0001-invoice-processing/visual-design.md`: the look. Use the one stylesheet and its
  classes and tokens (see `doc/design/styleguide.html`); no per-page styles or raw hex values.
  UI PRs include screenshots.
- If your implementation departs from plan.md, update plan.md in the same PR.

## Architecture
- `app/main.py`: `create_app(settings)` factory; state on `app.state` (settings, engine, sessions).
- `app/config.py`: settings from `INVOICE_FLOW_*` env vars. `app/db.py`: SQLite (WAL) engine, `Base`.
- `app/domain/`: `money.py` (`Money`, `MoneyType`), `states.py`, `actor.py`, `models.py` (`Invoice`),
  `transitions.py` (the only writer of invoice state). `app/audit/`: hash chain, `verify.py`.
  `app/jobs.py`: jobs table. `app/models.py` imports every model for Alembic.
- Planned: `app/tax/` (TaxRegime, IndiaGST),
  `app/extraction/`, `app/intake/`, `app/approval/`, `app/export/`, `app/templates/`.
- Migrations: Alembic (`alembic/`); one migration per DB slice.

## Conventions
- Money: integer paise in the DB, `Decimal` in Python. Never `float` for money.
- Every invoice state change goes through `app/domain/transitions.py`, which checks permissions and
  separation of duties and writes the audit event in the same transaction. No other code sets `state`.
- Every query for invoices, vendors or exports goes through the entity-scoped repository.
  Another entity's record gives 404, not 403.
- Bank details: encrypted at rest, masked in UI and exports, and never logged.
- Invoice text is untrusted. Model output only fills the extraction schema; it never drives actions.
- Tests mock the Claude API; only `make eval` calls it.
- Apply the skills in `.claude/skills/`: `gst-invoice-rules`, `data-protection`,
  `approval-and-audit`, `ux-standards`.

## Do not touch
`intent/**/intent.md`, `intent/**/spec.md`, `.claude/skills/**`, `.claude/hooks/**`,
`.claude/settings.json`. These are approved artifacts, policies and guardrails, and hooks block
edits to them. They change only by a human-reviewed PR.

## Things Claude gets wrong
(Add an entry when the same mistake happens twice.)
