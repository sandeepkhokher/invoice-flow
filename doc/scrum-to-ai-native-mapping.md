# From sprints and stories to intents, specs and plans

How the traditional Scrum setup (sprints, epics, stories, tickets) maps to the
artifacts of the AI-native SDLC playbook, and how story-based development works
in this repo.

## Is an intent a sprint, an epic or a story?

`intent.md` is closest to an **epic or PRD**: what is wanted and why, in the
originator's words. It is neither a sprint nor a story. It is not a one-off
overview of the whole system either. There is **one intent per change** that is
worth a product decision, so over time there are many.

## How the old pieces map

| Traditional (Scrum) | AI-native playbook | Notes |
|---|---|---|
| Initiative / epic / PRD | **`intent.md`** | What is wanted and why, in the originator's words |
| Refined requirements, acceptance criteria, design | **`spec.md`** | Requirements and design written together, with numbered requirements and acceptance criteria |
| Stories | **Slices in `plan.md`, each becoming a ticket** | Each slice is a GitHub Issue the agent picks up and turns into one PR |
| Tasks / sub-tasks | Steps inside one agent session | Too small to track; the agent works through them itself |
| Sprint | **No direct equivalent** | Work flows continuously; there is no timebox |
| Story points, estimation, refinement meetings | **Dropped** | They existed to align people over weeks of coding, which now takes hours |
| Sprint review / demo | PR review, verifier agent, screenshots | Happens per change instead of every two weeks |
| Retrospective | Updates to `CLAUDE.md`, skills and evals | Lessons go into files the agent reads every session |

## Why intents vary in size

An intent can come from an idea, a filed ticket, or an incident raised by
monitoring. Its size follows the decision being made:

- **Intent 0001** is epic-sized because it creates a new product.
- **Later intents are smaller**, for example `0002-tally-export`,
  `0003-near-duplicate-false-positives` or `0004-sso-login`.
- **Small, well-bounded fixes need no intent.** They come straight in as a
  ticket and a PR. Only larger work becomes an `intent.md`.

So the `intent/` folder becomes a history of product decisions:

```
intent/
  0001-invoice-processing/   intent.md  spec.md  plan.md    ← the epic
  0002-tally-export/         intent.md  spec.md  plan.md    ← a feature
  0003-dup-false-positives/  intent.md  spec.md  plan.md    ← written by the Stage 6 monitor
```

The whole-system view is not kept in one intent file. It comes from:

- the accepted specs added up,
- `CLAUDE.md`, which holds the architecture and conventions the agent reads
  every session, and
- the code itself.

## How story-based development fits in

Stories still exist. They are produced from the artifacts instead of being
written in refinement meetings:

```
intent.md (epic)
   │  product owner approves (merges the intent PR)
spec.md: requirements FR-1…FR-n, each with acceptance criteria
   │  product owner approves (merges the spec PR)
plan.md: work cut into vertical slices, for example:
   S1 Entities, users, roles and login
   S2 Upload intake → stored invoice
   S3 Claude extraction + confidence + review screen
   S4 GST validation layer
   S5 Duplicate detection
   S6 Approval routing + audit trail
   S7 Email and watched-folder intake
   S8 Export
   │  engineer approves; each slice becomes a GitHub Issue
Issue #12 "S4: GST validation", linked to spec FR-7..FR-10 and plan.md
   │  labelled agent:ready → the local runner picks it up
PR → AI review + human review → merged → issue closes
```

Each issue is a story in everything but name:

- it carries acceptance criteria copied from the spec,
- it links back to the spec and plan (the playbook's minimum "linkage" between
  the tracker and the repo), and
- it produces exactly one PR.

The plan also marks which slices are independent. Independent slices can run
at the same time in separate git worktrees (the playbook's parallel sessions).
Dependent slices run in order.

## What if we still want a cadence?

A team that wants a regular rhythm, for example a fortnightly demo to
stakeholders, can group issues into a GitHub milestone. That is a review rhythm
for humans, not a container for planning work.
