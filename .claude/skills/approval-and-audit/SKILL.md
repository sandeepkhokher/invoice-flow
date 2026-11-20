---
name: approval-and-audit
description: Approval routing, separation-of-duties and audit-trail policy for invoice-flow. Use whenever specifying, writing or reviewing code that changes an invoice's status, routes or records approvals, overrides flags, marks invoices paid or exported, or reads or writes the audit trail.
---
# Approval and audit (internal-controls policy)

Policy owner: Finance controller (acting: Sandeep Khokher).

## 1. Routing
- Each entity defines ordered approval tiers by grand-total amount (in INR), for example:
  tier 1 up to ₹50,000: one manager; tier 2 above ₹50,000: plus finance head; tier 3 above
  ₹5,00,000: plus CFO. Thresholds and approvers are configuration, editable only by admins.
- An invoice needs every tier up to and including its own band, in order.
- Tier configuration is captured on the invoice when it enters approval. Later config
  changes do not re-route invoices already in flight.

## 2. Separation of duties
- Whoever uploaded, edited extracted fields, or overrode a flag on an invoice may not approve it.
- One person may approve at most one tier of a given invoice, even if they hold several roles.
- Nobody can approve their own vendor record changes (for example bank details).
- Admins configure; they do not bypass. There is no "approve all" or force-approve path.

## 3. Flags and overrides
- An invoice with an open `block` flag cannot enter approval.
- A `block` may be overridden only by a user with `flags:override` on that entity, with a
  free-text reason of at least 15 characters. The override is an audit event and is shown
  to every approver.
- Approvers must explicitly acknowledge open `warn` flags when approving.

## 4. Changes after approval
- Approved, exported and paid invoices are read-only. A correction is a new state
  transition ("reopen") by an admin, with a reason; it voids prior approvals and restarts routing.

## 5. Audit trail
- Every state change, field edit (old value and new value, with restricted fields masked),
  flag raised or overridden, approval or rejection, bank-detail unmask, export and login is
  an audit event: who, what, entity, invoice, timestamp (UTC), and reason where applicable.
- The audit table is append-only: no update or delete path in the application, and a
  test proves it. Each event stores the hash of the previous event (hash chain), so tampering
  is detectable by a verification command.
- Audit events are written in the same database transaction as the change they describe.
