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
