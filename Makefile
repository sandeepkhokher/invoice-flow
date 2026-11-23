.PHONY: setup run migrate test lint fmt eval verify-audit runner runner-dry runner-loop

setup:        ## install dependencies
	uv sync

run:          ## start the app on http://127.0.0.1:8000
	uv run uvicorn app.main:create_app --factory --reload

migrate:      ## apply database migrations (Alembic) to the database under INVOICE_FLOW_DATA_DIR
	uv run alembic upgrade head

test:         ## run the test suite
	uv run pytest

lint:         ## lint and format check (CI runs this)
	uv run ruff check . && uv run ruff format --check .

fmt:          ## auto-format and fix lint
	uv run ruff format . && uv run ruff check --fix .

eval:         ## extraction eval (slice S10)
	@echo "not implemented yet: slice S10" && exit 1

verify-audit: ## verify the audit hash chain; exits non-zero if any event was tampered with
	uv run python -m app.audit.verify

runner:       ## one pass of the ticket runner over issues labelled agent:ready
	uv run python scripts/ticket_runner.py

runner-dry:   ## show which issues the runner would pick up
	uv run python scripts/ticket_runner.py --dry-run

runner-loop:  ## poll for agent:ready issues every 2 minutes
	uv run python scripts/ticket_runner.py --loop
