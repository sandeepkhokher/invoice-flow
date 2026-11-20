---
name: write-intent
description: Write or revise an intent.md (Stage 1 Plan artifact). Use whenever someone describes a new idea, problem, feature request, bug report or monitoring finding for invoice-flow and wants it captured, or asks to "write an intent", "capture this idea", or start a new change.
---
# Write an intent.md

An intent.md is a proto-spec in the originator's own words: what is wanted, why,
for whom, and under which constraints. It is the first artifact in the chain
intent.md -> spec.md -> plan.md -> PR, and the next stage reads it as input.

## Before writing
Ask the questions an analyst would, until the idea is concrete. Cover:
users and who is affected, what they cannot do today, what better looks like,
scope and non-goals, constraints (data, security, compliance, scale), and how
success will be measured. Do not invent answers; unknowns go under Open questions.

## Where it goes
- One folder per change: `intent/NNNN-short-slug/intent.md` (next free 4-digit number).
- Later stages add `spec.md` and `plan.md` to the same folder.
- Commit on a branch `intent/NNNN-short-slug` and open a PR. Merging the PR is the
  product owner's acceptance; closing it is a rejection. Never commit to main directly.

## Template
Use exactly these sections, in this order. Keep it under two pages; it is
intent, not design. No implementation choices (frameworks, tables, endpoints).

```markdown
# Intent NNNN: <title>
Author: <originator> (<role>). Drafted with: Claude. Status: draft | accepted | rejected.
Source: <conversation | ticket #N | monitoring alert> · Date: YYYY-MM-DD

## Problem
What people cannot do today, who feels it, and the cost of it.

## Proposed outcome
What better looks like, from the user's point of view.

## Affected users and systems

## Scope
### In scope
### Out of scope

## Constraints
Data, security, compliance, scale, cost.

## Success measures
Each one quantifiable and checkable without asking the author.

## Open questions
Anything unresolved. Stage 2 must answer or carry each one forward.
```
