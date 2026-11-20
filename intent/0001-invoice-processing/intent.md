# Intent 0001: Invoice processing for a multi-entity finance team
Author: Sandeep Khokher (product owner). Drafted with: Claude. Status: draft.
Source: conversation · Date: 2026-11-20

## Problem
A company with several legal entities receives vendor invoices as PDFs and images
through email, uploads, and shared folders. Today an accounts-payable (AP) clerk
re-keys each invoice by hand: vendor, GSTIN, invoice number, dates, line items and the
tax split. They check the GST rules from memory and chase the right approver for that
entity over email. This causes three costs:
- **Time:** a clean invoice still takes several minutes of typing and checking.
- **Leakage:** the same invoice arrives twice (re-sent by the vendor, or sent to two
  entities) and sometimes gets paid twice.
- **Errors and audit gaps:** wrong tax splits and typos reach the books, and there is
  no single record of who approved what, and when.

## Proposed outcome
Invoices arrive by any of the three routes and land in one queue, tagged to the right
entity. The system reads each invoice and fills in the fields, and the clerk only
confirms or corrects them. Before anyone approves, it flags likely duplicates and rule
violations. It then routes the invoice to that entity's approvers based on the amount.
Approved invoices can be exported in a form the accounting team can import. Every
invoice carries a full history: received, extracted, edited, flagged, approved and
exported, with who did each step and when.

## Affected users and systems
- **AP clerks:** review extracted invoices and fix errors.
- **Approvers:** managers, finance heads and the CFO, with different tiers for each
  entity.
- **Finance admin:** configures entities, approval tiers and vendors.
- **Accounting team:** consumes the exports.
- **Systems:** the email inbox that receives invoices, shared or watched folders, the
  Claude API (extraction), and the downstream accounting system (via file export only).

## Scope
### In scope
- Multiple entities, each with its own approval tiers by amount (for example up to
  ₹50k: manager; above: manager plus finance head; above ₹5L: plus CFO).
- Intake from web upload, a polled email inbox, and a watched folder; PDF and common
  image formats.
- AI extraction of header fields and line items, with a confidence level for each field.
- A tax layer that can take more regimes later, with India GST shipped first: GSTIN
  format and checksum, the CGST+SGST vs IGST split by place of supply, HSN/SAC presence,
  and arithmetic checks.
- Duplicate detection: exact matches and near-matches (same vendor with a similar
  number, amount or date).
- Approval workflow with an audit trail.
- CSV/JSON export of approved invoices, and marking invoices as paid by hand.

### Out of scope (v1)
- Pushing data into Tally, Zoho Books or QuickBooks, and generating bank payment files.
- Purchase-order or goods-receipt matching (2- and 3-way match).
- Other tax regimes, which are designed for but not shipped.
- A vendor portal, multiple currencies beyond recording the currency, and mobile apps.

## Constraints
- Invoice content may be sent to the Claude API for extraction. Original files and data
  stay on the company's own machine or server.
- Bank details (account number, IFSC) are masked in the UI, logs and exports, except for
  roles explicitly authorized to see them. They never appear in application logs.
- Users see only the entities they belong to.
- Every state change is recorded in an audit trail that users cannot edit.
- Volume: up to 2,000 invoices a month across all entities, on a single machine. The
  first deployment is local.

## Success measures
1. **Extraction accuracy:** at least 95% field-level accuracy on a labelled test set of
   real-world-style invoices, covering vendor, GSTIN, invoice number, invoice date,
   subtotal, tax amounts and total. Measured by an automated eval.
2. **Touch time:** a clean invoice goes from intake to "ready for approval" in under 1
   minute with no manual edits.
3. **Duplicate catch:** 100% of seeded duplicates and near-duplicates in the test set
   are flagged before approval.

## Open questions
1. How does an invoice get assigned to an entity: by the buyer GSTIN on the invoice, by
   the receiving inbox or folder, or by the clerk?
2. What exactly counts as a near-duplicate (thresholds for number similarity, amount
   and date), and can a clerk override a flag with a reason?
3. If one person holds two approval tiers, may they approve twice, or must the tiers be
   different people?
4. Which column layout should the export use? The accounting team has not named a
   target system yet.
5. Where do vendor master data (names, GSTINs, bank details) come from: built up from
   invoices, or imported?
6. How long are original files and the audit trail retained?
7. Login: local accounts for v1, or single sign-on (SSO) from day one?
