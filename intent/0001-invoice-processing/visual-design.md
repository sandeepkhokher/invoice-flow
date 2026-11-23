# Visual design 0001: how invoice-flow looks
Status: proposed (awaiting product-owner review). Date: 2026-11-23.
Produced by: Claude Opus 5.5, in-session, after the product owner saw the unstyled placeholder
and pointed out that no visual design had been decided. Stage 2 (Design) artifact, alongside
`spec.md` (what), `screens.md` (which screens and their structure) and the `ux-standards` skill
(behaviour rules). This file decides the look.
Implementation: `app/static/css/invoice-flow.css` (tokens and components; moved from `doc/design/` in S5.1) and
`doc/design/styleguide.html` (every component with real-looking content; open it in a browser).

Everything here is a proposal; merging the PR accepts it. Comment on anything to change.

---

## 1. Brief

- **Who:** AP clerks confirming extracted fields all day on a desktop; approvers acting from
  their phones; accountants exporting; admins configuring. Indian finance teams.
- **The job of the UI:** let a clerk confirm an invoice against its document quickly and
  without misreading a character. Dense, calm, legible. Nothing decorative competes with the
  numbers.
- **Where the look comes from:** the Indian accounts office. The *bahi-khata* ledger (red
  binding, red margin line, ruled columns, double rule under a total), carbon-copy invoice books
  (blue carbon), and the rubber stamps that mark an invoice received, approved or paid.

## 2. Decisions

| Area | Decision | Why |
|---|---|---|
| CSS approach | One hand-written stylesheet with CSS custom properties (tokens); no framework, no build step | Matches the HTMX/Jinja stack (spec §5.1); every template uses the same classes |
| Typeface | **Atkinson Hyperlegible Next** for UI, **Atkinson Hyperlegible Mono** for GSTINs, invoice numbers and keys | Designed for character distinction: 0/O, 1/l/I, 5/S, 8/B are unmistakable, which is exactly what reviewing GSTINs and invoice numbers needs. One family keeps it quiet |
| Fonts | Self-hosted in `app/static/fonts/` (SIL Open Font License); no Google Fonts call from the app | Finance app on a local network; no third-party requests (data-protection). The style guide loads the same files through the app stylesheet |
| Numbers | Tabular, lining figures everywhere; amounts right-aligned | Columns of rupees line up |
| Signature element | **Invoice states as ink stamps** (double-ruled outline, state colour, word always present); the large stamp on an invoice header is tilted 3° and "stamps in" once when the state changes | The one memorable thing, taken from how paper invoices are actually marked. Everything else stays quiet |
| Structure | Ledger rules instead of cards: ruled tables, a 2 px ink rule under headers, a double rule under totals, and a red ledger-margin line between menu and content | Encodes structure (header, total, margin) rather than decorating; avoids the generic card dashboard |
| Elevation | No drop shadows except the document page in the viewer | Paper on a desk |
| Motion | Only the stamp-in on a state change; honours `prefers-reduced-motion` | Motion answers an action, nothing else |

## 3. Tokens

### Colour
| Token | Hex | Use | Contrast |
|---|---|---|---|
| `--paper` | #F7F8F5 | Page background (cool ledger paper, not cream) | — |
| `--sheet` | #FFFFFF | Forms, tables, viewer | — |
| `--ink` | #1C2433 | Text | 14.6:1 on paper |
| `--ink-soft` | #4A5466 | Secondary text, labels | 7.2:1 |
| `--carbon` | #2340A0 | Primary buttons, links, focus ring, in-approval state | 9.1:1 on white |
| `--bahi` | #A51C30 | Brand, ledger margin, blocked flags, rejected/discarded | 6.4:1 on its wash |
| `--turmeric` | #8A5A00 | Warnings, low confidence, needs-review | 5.3:1 on its wash |
| `--cleared` | #2E6A3E | Approved, exported, paid, valid | 6.5:1 on white |
| `--rule` | #C9D3C5 | Table lines, card edges (decorative) | — |
| `--control` | #7A8777 | Input and button borders | 3.8:1 (WCAG 1.4.11 needs 3:1) |

Severity and state never rely on colour alone (ux-standards): every flag has an icon and a
word, every stamp has the state name.

### Type scale (ratio 1.25, base 16 px)
14 px tables and help text · 16 px body and inputs · 20 px section headings · 25 px page titles
· 31 px the single amount on the Approve screen. Weights 400 and 700 only. Sentence case
everywhere; no all-caps labels.

### Space and shape
4 px base: 4, 8, 12, 16, 24, 32, 48. Radius 4 px on controls, 3 px on stamps, none on tables.
Controls are at least 40 px tall (48 px for Approve/Reject on phones).

## 4. Components (see the style guide)

| Component | Class | Notes |
|---|---|---|
| Layout shell | `.shell .topbar .sidenav main` | Implements screens.md §1.1; red margin line; menu becomes a scrolling row under 768 px |
| Page header | `.page-head` | Title, state stamp, actions on the right |
| Buttons | `.btn .btn-primary .btn-danger` | One primary per screen; disabled primary keeps a title explaining why |
| Fields | `.field` (+ `data-invalid`, `data-confidence="low"`) | Label above, help below, error in bahi red next to the field; low confidence = dashed turmeric outline + wash |
| Ledger table | `.table-wrap .ledger` | Sticky header, ink rule under header, double rule under totals, right-aligned amounts |
| Flags | `.flag .flag-block/.flag-warn/.flag-info` | Icon + bold title + what/why/how-to-fix text |
| Stamps | `.stamp[data-state]`, `.stamp-lg` | All 11 states of spec §5.2 mapped to four colours |
| Work list | `.worklist` | My work as ledger rows (task, count, action), not cards |
| Review | `.review .docview .fieldset .actionbar` | Document left (sticky), fields right; stacks below 1100 px |
| Approve | `.approve` | Phone-first, one large amount, two big buttons |
| Empty state | `.empty` | Says what will appear and offers the action |
| Shortcuts | `kbd` | Listed in the review action bar |

## 5. Rules for builders (tickets and reviewers)

1. Link `app/static/css/invoice-flow.css` from `base.html`; do not write per-page styles.
   A new component goes into the stylesheet and the style guide in the same PR.
2. Use tokens, never raw hex values, in templates and CSS.
3. Money with `₹` and Indian grouping, dates as `20-Nov-2026`, GSTINs grouped in mono
   (ux-standards). Amount columns get `.amount`.
4. Each screen PR includes a screenshot at 1280 px, plus 375 px for SC-21 and the shell.
5. Copy is sentence case, active voice, and names the action ("Submit for approval",
   "Save registration"); errors say what is wrong and how to fix it.

## 6. Open points (PO to confirm)
- The stamp signature and the bahi-khata palette as the product's identity.
- Atkinson Hyperlegible as the only typeface.
- Light theme only in v1 (screens.md §7 already lists dark theme as out of scope).
