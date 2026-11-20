---
name: ux-standards
description: UX and presentation standards for invoice-flow screens. Use whenever specifying, building or reviewing any page, template, form, table, or display of money, dates, confidence or flags.
---
# UX standards

Policy owner: Product owner (Sandeep Khokher).

## Principles
- The clerk's job is to **confirm**, not to type. Every review screen shows the original
  document beside the extracted fields, and clicking a field highlights where it came from
  when that is known.
- Keyboard first: Tab moves through fields in reading order, Enter confirms the invoice,
  and shortcuts are listed on the page.
- One primary action per screen.

## Formatting
- Money in INR with Indian digit grouping and the rupee sign: `₹1,23,456.00`. Other
  currencies show their ISO code: `USD 1,234.00`.
- Dates as `20-Nov-2026` (unambiguous); store ISO 8601. Times in the user's local zone
  with the zone shown.
- GSTINs in monospace, grouped for reading: `27 AAPFU0939F 1Z V`.

## Confidence and flags
- Low-confidence fields (below the configured threshold) are outlined and listed at the
  top of the form, so the clerk checks those first.
- Flag severity uses an icon and text as well as colour (`Blocked`, `Warning`, `Info`),
  never colour alone. Every flag says what is wrong, why it matters, and how to fix it.

## Accessibility and layout
- WCAG 2.1 AA: labels on every input, visible focus, 4.5:1 contrast, and announced status
  changes (ARIA live regions on HTMX swaps).
- Works from 1280 px wide. The review screen is desktop-first; the approval screen also works
  at 375 px, because approvers act from their phones.
- No full-page reloads for in-place actions (HTMX partial updates), and every action gives
  visible feedback within 200 ms.
