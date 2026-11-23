# invoice-flow

Accounts-payable invoice processing for multi-entity companies: intake (upload, email,
watched folder), AI extraction, GST-first validation, duplicate detection, per-entity
approval routing and export for accounting.

This repo is also a worked example of the
AI-Native SDLC Playbook (Anthropic): every change moves through
committed artifacts that the next stage reads.

| Stage | Artifact | Where | Gate |
|---|---|---|---|
| 1 Plan | `intent.md` | `intent/NNNN-slug/` | Product owner merges the intent PR |
| 2 Design | `spec.md` | same folder | Product owner merges the spec PR |
| 3 Build | `plan.md`, code, tests | same folder + `src/` | Engineer accepts the plan |
| 4 Test | test output, evals | CI | Checks green |
| 5 Deploy | PR + review findings | GitHub | Code owner approval |
| 6 Maintain | new `intent.md` from monitoring | `intent/` | Triage |

Tickets live in GitHub Issues. The git history of `intent/` is the audit trail.

## Run locally

Needs Python 3.13 and [uv](https://docs.astral.sh/uv/).

```
make setup          # install dependencies
make migrate        # create the SQLite database under ./data
make run            # http://127.0.0.1:8000 (health check: /health)
make test           # test suite;  make lint for lint + format check
make gen-invoices   # write the synthetic sample invoices to evals/data/
```

Settings come from `INVOICE_FLOW_*` environment variables (see `app/config.py`). The app is
being built slice by slice (see `intent/0001-invoice-processing/plan.md`); screens appear as
their slices merge (`screens.md` §5).
