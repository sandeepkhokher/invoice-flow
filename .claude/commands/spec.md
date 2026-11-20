---
description: Stage 2 (Design). Turn an accepted intent.md into spec.md with flagged concerns.
argument-hint: <intent folder, e.g. intent/0001-invoice-processing>
---
Read $ARGUMENTS/intent.md and produce a requirements and design spec for integrating it
into our existing codebase (or for a new codebase if none exists yet). Apply the skills
available to you (gst-invoice-rules, data-protection, approval-and-audit, ux-standards) so
the spec conforms to our compliance rules, security policies and UX standards. Read
CLAUDE.md if it exists.

Write it as $ARGUMENTS/spec.md, ready to hand to the engineering team, with:
- a header recording this command, the intent commit and the commit SHA of .claude/skills
  in force;
- an answer, or an explicit carry-forward, for every open question in intent.md;
- numbered functional requirements (FR-n), each with testable acceptance criteria;
- non-functional requirements, the domain model, state machine and key designs;
- a mapping from every success measure in intent.md to how it will be verified;
- an "Areas of concern" section listing, for each concern, the policy or question involved,
  its policy owner, the options, a recommendation, and the decision needed. Describe clearly
  any place where you cannot satisfy contradicting policies.

Do not write implementation code. Commit on branch spec/<slug> and open a PR labelled
stage:design; merging it is the product owner's acceptance.
