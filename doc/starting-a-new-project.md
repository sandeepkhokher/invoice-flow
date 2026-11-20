# Starting a new project the AI-native way

How to start a new product with Claude using the AI-native SDLC, without needing the
playbook PDF to hand.

## The approach in one line

> **Every stage ends by committing one file that the next stage reads, and nothing moves
> forward until a person approves that file.**

## How it maps to the traditional flow

| You used to… | Now you… | File | Your gate |
|---|---|---|---|
| Brainstorm in meetings | Brainstorm with Claude, which asks the analyst's questions | `intent.md` | Merge the intent PR |
| Write requirements, then design | Have Claude write both in one pass, constrained by your policies | `spec.md` (+ policy skills) | Decide the flagged concerns, merge |
| Plan and create tickets | Have Claude plan in plan mode; each slice becomes a ticket | `plan.md` + issues | Accept the plan |
| Code | Let agents build tickets, each checking its own work with tests | code + tests | — |
| Review and QA | Have AI review first; you judge intent and risk | PR + findings | Approve the PR |
| Wait for bugs | Let a monitor write the next `intent.md` when a metric drifts | new `intent.md` | Triage |

See also [scrum-to-ai-native-mapping.md](scrum-to-ai-native-mapping.md) for how epics,
stories and sprints map to these files.

## How to start

If the `ai-native-sdlc` skill is installed (see the last section), say:

> "New product: **[product idea]**. Use the AI-native SDLC skill."

Without the skill, say:

> "Let's build **[product idea]** using the AI-native SDLC. Start with Stage 1: interview
> me as an analyst, then write `intent.md` and open it as a PR. Stop at each gate for my
> approval."

Then at each gate, one line moves things forward:

1. "Intent merged. Do Stage 2: write the policy skills we need, then `spec.md` with concerns."
2. "Spec merged. Do Stage 3: `CLAUDE.md`, plan mode, `plan.md`, and tickets."
3. "Plan accepted. Build the tickets."
4. "Set up the review, approval gates and CI."
5. "Set up monitoring that writes intents."

## What to expect at each stage

| Stage | Claude does | You do |
|---|---|---|
| 1 Plan | Asks analyst questions: users, problem, scope, constraints, success measures. Writes `intent.md` | Answer; correct the draft; merge |
| 2 Design | Writes policy skills (security, compliance, UX, domain rules), then `spec.md` with numbered requirements and flagged concerns | Provide real policies if you have them; decide each concern; merge |
| 3 Build | Writes `CLAUDE.md`; plans in plan mode; commits `plan.md`; creates one ticket per slice; builds | Challenge the plan (what could break, riskiest step, alternatives); accept it |
| 4 Test | Runs tests on every change; builds evals for any AI feature | Check the evidence pasted in PRs |
| 5 Deploy | Reviews PRs against `REVIEW.md`, spec and plan; fixes `@claude` comments; sets up gate hooks and CI | Approve PRs; authorise production releases |
| 6 Maintain | A script watches a metric; when it drifts, Claude diagnoses and writes a new `intent.md` | Triage: fix now, schedule, or dismiss |

## Two things to know

- **Some things happen once per project:** the repo, the `intent/` folder, the policy
  skills, `CLAUDE.md`, hooks, CI. Later changes reuse them, so the second intent is much
  faster than the first.
- **A small fix skips the early stages.** A bug or tweak goes straight to a ticket and a
  PR. Only work that needs a product decision starts with an intent.

## Ground rules worth stating up front

- **Bring your real policies.** If your organisation has a security policy, approval matrix
  or brand guide, share it at Stage 2. Otherwise Claude drafts them, and anything it drafts
  is marked `PROPOSED` until you approve it.
- **Gates are yours.** Claude stops at each gate and does not merge or approve on your
  behalf unless you say so.
- **Ask where things come from.** Claude should say which parts come from the playbook,
  which from law or standards, and which are its own judgment.

## The `ai-native-sdlc` skill

The approach is packaged as a personal Claude Code skill at
`~/.claude/skills/ai-native-sdlc/`, available in every project on the machine where it is
installed. It holds the stages, gates and rules above, plus templates for `intent.md`, the
`/spec` command, `plan.md`, tickets and `REVIEW.md`. To use it on another machine, copy that
folder to `~/.claude/skills/` there.
