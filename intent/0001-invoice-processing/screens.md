# Screens 0001: screen map for invoice-flow
Status: proposed (awaiting product-owner review). Date: 2026-11-23.
Produced by: Claude Opus 5.5, in-session, after the product owner pointed out that the spec
had no single screen inventory. Part of the Stage 2 (Design) artifacts; an addendum to
`spec.md`, which it does not change.
Inputs: `spec.md` §2 (roles), §3 (FRs), §5.2 (states); `.claude/skills/ux-standards`;
`plan.md` slices S5–S15.

Every UI ticket builds the screens listed for its slice (§5), to the layouts here. Screen IDs
(SC-n) are cited in tickets, PRs and tests, like FR IDs. Items marked **PO to confirm** are
Claude's proposals, not decisions taken elsewhere.

---

## 1. Shape of the UI

- **One web app, one login, menus by role.** There is no separate admin app: admin pages
  are a section of the same app that only users with `admin` on an entity see. A person can
  hold several roles; the menu is the union of what their roles allow. **PO to confirm.**
- **Server-rendered pages with HTMX** (spec §5.1): Jinja templates, partial updates for
  in-place actions, no JavaScript framework. Works without a build step.
- **Entity switcher** in the header: users with grants on several entities pick one, or
  "All my entities" for lists and the queue. Pages never show data from an entity the user
  has no grant on (FR-3, data-protection §2). **PO to confirm.**
- **Landing page:** everyone lands on *My work* (SC-2), which shows the tasks for their
  roles. Admins with no other role land on *Admin* (SC-40).

### 1.1 Layout shell (every page after login)

```
+--------------------------------------------------------------------------------+
| invoice-flow   [Entity: Acme Mfg Pvt Ltd v]          [Bell 3]  [S. Khokher v]  |
+----------------+---------------------------------------------------------------+
| My work        |  Page title                                  [Primary action] |
| Invoices       |  ------------------------------------------------------------ |
| Upload         |                                                               |
| Approvals      |  page content                                                 |
| Export         |                                                               |
| Vendors        |                                                               |
| Audit          |                                                               |
| Admin  >       |                                                               |
+----------------+---------------------------------------------------------------+
```
Menu items appear only for roles that can use them (§2). Below 768 px the side menu
collapses into a menu button. Status messages use an ARIA live region (ux-standards).

## 2. Who sees what

| Menu item | clerk | approver | accountant | admin | auditor |
|---|---|---|---|---|---|
| My work (SC-2) | ✓ | ✓ | ✓ | ✓ | ✓ |
| Invoices (SC-3, SC-4) | ✓ | ✓ | ✓ | ✓ | ✓ (read-only) |
| Upload (SC-10) | ✓ | | | | |
| Approvals (SC-20) | | ✓ | | | |
| Export (SC-30, SC-31) | | | ✓ | | |
| Vendors (SC-43) | ✓ (read) | | ✓ (read) | ✓ | ✓ (read) |
| Audit (SC-50) | | | | | ✓ |
| Admin (SC-40–SC-47) | | | | ✓ | |

Bank details are masked everywhere unless the user holds `bank_details:view` and clicks
"Show", which is audited (data-protection, FR-50).

## 3. Screen inventory

| ID | Screen | Route | Roles | Purpose and contents | Primary action | FRs | Slice |
|---|---|---|---|---|---|---|---|
| SC-1 | Login | `/login` | all | Email + password; generic error on failure; lockout after repeated failures | Sign in | DP §3 | S5 |
| SC-2 | My work | `/` | all | Task cards per role: *to review* (clerk), *to approve* (approver), *to export / to mark paid* (accountant), *quarantine* (admin). Counts by state and age of oldest item | Open the oldest task | FR-65 | S5 shell; S15 counts |
| SC-3 | Invoice list | `/invoices` | all | Table: supplier, number, date, total, entity, state, flags, age. Filters: state, entity, supplier, date range, has-flags. Search by number or GSTIN | Open invoice | FR-61 | S7 basic; S15 filters |
| SC-4 | Invoice detail | `/invoices/{id}` | all | Read-only summary, document, flags, approvals so far, **History** tab (audit events). Admin: *Reopen* (reason required) | Context: Review / Approve / Reopen | FR-44, FR-61 | S11; S12 reopen |
| SC-5 | Notifications | `/notifications` | all | In-app list: assigned for approval, rejected back to you, extraction failed | Open item | FR-40 | S12 |
| SC-6 | Account | `/account` | all | Name, change password, my roles per entity (read-only) | Save | FR-3 | S5 |
| SC-10 | Upload | `/upload` | clerk | Drag-and-drop many files; entity picker or "auto-assign"; per-file result (accepted, or rejected with the reason) | Upload | FR-10, FR-13 | S7 |
| SC-11 | **Review** | `/invoices/{id}/review` | clerk | Document viewer beside editable fields; low-confidence fields first; flags with fix guidance; line items; edits re-validate in place. For `extraction_failed`: *Retry* or enter fields by hand | Submit for approval (disabled while a `block` is open) | FR-21–22, FR-25, FR-35–37 | S11 |
| SC-12 | Duplicate compare | `/invoices/{id}/duplicates` | clerk | This invoice beside each candidate (fields that match are highlighted). Cross-entity candidates show only "possible duplicate in another entity" (FR-32) | Discard as duplicate (reason) or Not a duplicate (override, needs `flags:override`) | FR-30–33 | S11 |
| SC-20 | Approval inbox | `/approvals` | approver | My pending approvals: supplier, total, entity, tier, warnings, waiting since | Open | FR-40 | S12 |
| SC-21 | **Approve** | `/approvals/{id}` | approver | Summary (supplier, number, date, total, tax), document, warnings to acknowledge, earlier approvals. Works at 375 px | Approve / Reject (reason) | FR-41–43 | S12 |
| SC-30 | Export | `/exports/new` | accountant | Entity + date range → preview of the invoices to export; CSV/JSON; "include bank details" only with `bank_details:view` | Export | FR-50–51 | S13 |
| SC-31 | Export batches | `/exports` | accountant | Past batches: who, when, count; re-download (audited) | Download | FR-51 | S13 |
| SC-32 | Mark paid | `/payments` | accountant | Exported invoices; select several, enter payment date and reference | Mark paid | FR-52 | S13 |
| SC-40 | Admin: entities | `/admin/entities` | admin | Entities (legal name, PAN) and their GST registrations (GSTIN, state, address); GSTIN validated on save | Add entity / registration | FR-1 | S5 |
| SC-41 | Admin: users | `/admin/users` | admin | Users; per-entity role grants; extra permissions (`flags:override`, `bank_details:view`); deactivate | Invite / Save grants | FR-3 | S5 |
| SC-42 | Admin: approval tiers | `/admin/entities/{id}/tiers` | admin | Ordered amount bands with approvers per tier; contiguity validation; warning when a tier has too few approvers to satisfy separation of duties (C4) | Save | FR-2, C4 | S6 |
| SC-43 | Vendors | `/vendors` | admin (edit), others read | Vendor master by GSTIN; masked bank details; bank change needs an admin other than the invoice's clerk; CSV import with a preview | Import / Save | FR-55–56 | S8 |
| SC-44 | Admin: intake sources | `/admin/intake` | admin | IMAP inboxes and watched folders per entity; sender allow-list; last poll status | Add source | FR-11–12, C7 | S14 |
| SC-45 | Admin: GST rate table | `/admin/tax-rates` | admin | Rates with effective-from dates; changes audited | Add rate | FR-27 | S6 (deferred from S2) |
| SC-47 | Admin: quarantine | `/admin/quarantine` | admin | Emails from senders not on the allow-list; release or delete | Release | C7 | S14 |
| SC-50 | Audit | `/audit` | auditor | Search events by invoice, user, action, date; *Verify chain* runs `verify-audit-chain` and shows the result | Search / Verify | FR-61 | S15 |

## 4. Key layouts

### SC-11 Review (desktop, 1280 px and up)

```
+--------------------------------------------------------------------------------+
| < Back   INV-2026-0042 · Shree Ganesh Traders · Acme Mfg   [needs_review]      |
| ! Blocked: CGST+SGST charged on an inter-state supply. Fix the tax head or ... |
| ~ Warning: possible duplicate of INV-2026-0041 (12-Nov-2026)  [Compare]        |
+--------------------------------------+-----------------------------------------+
|                                      | Check first (low confidence)            |
|   [ document viewer: PDF / image ]   |  Invoice date   [ 12-Nov-2026 ]  low  p1 |
|   page 1 of 2   [-] [+] [rotate]     |  Total          [ ₹1,18,000.00 ] low  p2 |
|                                      | Supplier                                |
|   (clicking a field highlights       |  GSTIN  27 AAPFU0939F 1Z V   ok          |
|    where it was found, if known)     |  Name   [ Shree Ganesh Traders ]         |
|                                      | Invoice                                  |
|                                      |  Number [ INV-2026-0042 ]  Place [27]    |
|                                      | Line items          (table, editable)   |
|                                      | Tax summary   Taxable ₹1,00,000.00       |
|                                      |               IGST 18% ₹18,000.00         |
+--------------------------------------+-----------------------------------------+
| Shortcuts: Tab next field · Ctrl+Enter submit · ? all shortcuts                |
|                        [Discard…]                  [ Submit for approval ]     |
+--------------------------------------------------------------------------------+
```

### SC-21 Approve (phone, 375 px)

```
+-----------------------------+
| < Approvals        Tier 1/2 |
| Shree Ganesh Traders        |
| INV-2026-0042 · 12-Nov-2026 |
| ₹1,18,000.00                |
| Taxable ₹1,00,000 · IGST 18%|
| Acme Mfg Pvt Ltd            |
| ~ Warning: possible dup     |
|   [x] I have checked this   |
| [ View document ]           |
| Reviewed by: R. Mehta       |
|                             |
| [ Reject… ]   [ Approve ]   |
+-----------------------------+
```
Approve stays disabled until every warning is acknowledged (FR-41). Reject asks for a reason.
"Reviewed by" shows the clerk who submitted the invoice, so the approver knows who to ask;
separation of duties is enforced on the server either way. **PO to confirm** (proposed default).

### SC-2 My work

```
+---------------------------------------------------------------+
| My work                           Entity: All my entities      |
| +---------------+  +---------------+  +---------------+        |
| | To review  12 |  | To approve  4 |  | To export   9 |        |
| | oldest 3 days |  | oldest 1 day  |  | oldest 2 days |        |
| +---------------+  +---------------+  +---------------+        |
| Extraction failed: 1   Blocked: 3   In approval: 7             |
| Next up: INV-2026-0042 · Shree Ganesh Traders · ₹1,18,000.00   |
+---------------------------------------------------------------+
```

## 5. Screens by slice (what each ticket builds)

| Slice | Screens |
|---|---|
| S5 | Layout shell (§1.1) with role menus and entity switcher; SC-1, SC-2 (empty cards), SC-6, SC-40, SC-41 |
| S6 | SC-42, SC-45 |
| S7 | SC-10, SC-3 (basic list, no filters) |
| S8 | SC-43 |
| S9 | none (extraction state shows in SC-3) |
| S11 | SC-11, SC-12, SC-4 (without reopen) |
| S12 | SC-20, SC-21, SC-5; reopen on SC-4 |
| S13 | SC-30, SC-31, SC-32 |
| S14 | SC-44, SC-47 |
| S15 | SC-2 counts and ages, SC-3 filters and search, SC-50 |

## 6. Patterns every screen follows

- **Formatting, flags and accessibility:** as in `ux-standards` (₹ with Indian grouping,
  `20-Nov-2026` dates, grouped GSTINs, icon + text for severity, WCAG 2.1 AA).
- **Empty states** say what will appear and how to make it appear ("No invoices yet.
  Upload some or connect an inbox").
- **Errors** appear next to the field, and the form keeps what the user typed.
- **Not found and no access look the same** (404), so the UI never reveals another entity's
  records (CLAUDE.md conventions).
- **Destructive or irreversible actions** (discard, reject, reopen, override) open an inline
  confirm with a required reason; no browser `confirm()` dialogs.
- **Tests:** each screen has at least one route test per role that can see it and one for a
  role that cannot (403/404), plus the screen's primary action.

## 7. Out of scope for v1 (PO to confirm)

Reports and analytics beyond SC-2 counts; a dark theme; languages other than English; a
native mobile app (SC-21 covers approvers on phones); user self-registration (admins invite).
