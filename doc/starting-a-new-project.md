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
| 2 Design | Writes policy skills (security, compliance, UX, domain rules), then `spec.md` with numbered requirements and flagged concerns. For a product with a UI, also `screens.md` (screen map) and `visual-design.md` with a stylesheet and a style guide page | Provide real policies and any brand guide; decide each concern; look at the style guide; merge |
| 3 Build | Writes `CLAUDE.md`; plans in plan mode; commits `plan.md`; creates one ticket per slice; builds | Challenge the plan (what could break, riskiest step, alternatives); accept it |
| 4 Test | Runs tests on every change; builds evals for any AI feature | Check the evidence pasted in PRs |
| 5 Deploy | Reviews PRs against `REVIEW.md`, spec and plan; fixes `@claude` comments; sets up gate hooks and CI | Approve PRs; authorise production releases |
| 6 Maintain | A script watches a metric; when it drifts, Claude diagnoses and writes a new `intent.md` | Triage: fix now, schedule, or dismiss |

## UI design is part of Stage 2 (our addition)

The playbook does not mention screens or visual design; it has a UX/brand policy skill and
the spec. On invoice-flow that was not enough: the spec listed requirements but no screens,
and nothing said how the app should look, so every UI ticket would have invented its own.
For any product with a user interface, Stage 2 therefore ends with two more artifacts,
each approved by PR before the first UI ticket runs:

- **`screens.md`**: which screens exist, who sees each, what is on it, its one main action,
  which ticket builds it, and wireframes of the key screens.
- **`visual-design.md`**: the look (typefaces, colours with measured contrast, spacing,
  components, one signature element), delivered as a real stylesheet and a style guide page
  you open in a browser, with screenshots. You approve what you can see.

If you already have a brand or design system, give it to Claude first; otherwise Claude
proposes one, marked "PO to confirm". UI tickets then cite screen IDs and attach screenshots.

## Build a walking skeleton first (our addition)

invoice-flow first planned its build bottom-up: libraries in wave 1, login and admin next,
the workflow last. After 5 of 15 slices, roughly 10,000 lines and 700 tests, the product owner
could click only a login page and two admin pages. The rule we now follow:

- The plan names a **walking skeleton**: the thinnest path through the whole main journey
  (for invoice-flow: upload → extract → review → approve → export → paid), built first.
- Depth is deferred with stubs and defaults: a demo mode for the AI call so it runs without an
  API key, one default approval tier instead of the tier admin screen, no bank-detail columns
  until vendors exist.
- The plan states the skeleton's proof as a click-path in the running app.
- At every gate Claude says what you can run or click now, and how many slices remain before
  the journey works end to end.
- Full independent reviews go to risky slices (security, money, model output, permissions);
  the rest get CI, the runner's gate, the verifier and a quick read.

## Where the policy skills come from (Stage 2)

Nothing in the intent instructs Claude to write skills. The playbook requires policies to
exist as skills before the spec is written, so the spec is constrained by them. The intent
only supplies the topics. Claude crosses the playbook's categories with what the intent raises:

| Category | Look in the intent for | invoice-flow example |
|---|---|---|
| Compliance / domain rules | laws, regulations, industry rules, calculations | `gst-invoice-rules` |
| Security / data protection | personal or financial data, tenants, external APIs, file input | `data-protection` |
| Internal controls | approvals, audit trail, who may do what | `approval-and-audit` |
| UX / brand | always one, from the org's design or brand guide | `ux-standards` |

The order is: the intent is accepted → policy skills are written and committed → the spec is
written against them, and its header cites the skills commit → the product owner reviews the
skills and the spec together in the same PR.

The rules inside each skill should come from the policy's real owner and source document.
When there is none, Claude drafts the rules from your answers, the relevant law or standards,
and its own judgment, and must mark them `PROPOSED` with the source of each rule until you
approve them. A skill is advisory, so any rule that must always hold also needs a test or a
hook (Stages 3–5).

In invoice-flow, the skills were drafted by Claude without a source document and committed
before that rule existed. Treat them as proposals until they are reviewed in PR #3.

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
