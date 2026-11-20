---
name: gst-invoice-rules
description: India GST rules for validating purchase (vendor) invoices. Use whenever specifying, writing, reviewing or testing code that validates invoices, tax amounts, GSTINs, place of supply, HSN/SAC codes, tax rates, or duplicate invoice numbers in invoice-flow.
---
# GST invoice rules (compliance policy)

Policy owner: Finance controller (acting: Sandeep Khokher). Source of truth: CGST Act 2017,
CGST Rules (Rule 46 tax invoice contents), GST Council rate notifications. Change this file
only with the policy owner's sign-off in the PR.

These rules produce **flags** on an invoice. They never auto-reject an invoice and never
auto-approve one. A person decides.

Severity levels: `block` (cannot be approved until fixed or overridden with a reason),
`warn` (approver must acknowledge), `info` (shown, no action needed).

## 1. GSTIN
- Format: 15 characters, `^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$`.
  Characters 1-2 are the state code, 3-12 the PAN, 13 the entity number, 14 is `Z`,
  15 is the check character.
- Check character: map `0-9A-Z` to 0..35. For the first 14 characters, multiply by a
  factor alternating 1, 2, 1, 2... (starting at 1), and add `product // 36 + product % 36`
  to a running sum. Check value = `(36 - sum % 36) % 36`, mapped back to a character.
- State code must be in the maintained state-code table (01-38, 97 Other Territory,
  99 Centre Jurisdiction). Keep the table as data, not code.
- Invalid supplier GSTIN: `block`. Buyer GSTIN not one of our entity registrations: `block`
  (it may be addressed to someone else). Missing supplier GSTIN: `warn` (unregistered
  supplier; no input tax credit).

## 2. Mandatory invoice contents (Rule 46)
Supplier name, address and GSTIN; invoice number; invoice date; recipient name, address
and GSTIN; HSN/SAC per line; description; quantity (goods); taxable value; tax rate and
amount per tax head; place of supply (for inter-state); reverse-charge indicator where
applicable. Missing invoice number, date or total: `block`. Others missing: `warn`.
- Invoice number: at most 16 characters; letters, digits, `-` and `/` only. Violations: `warn`.

## 3. Tax heads and place of supply
- Intra-state (supplier state code == place of supply state): CGST + SGST (or UTGST for
  union territories without a legislature), equal rates, each half of the total rate.
- Inter-state (different states): IGST only, at the full rate.
- Wrong head for the supply type (for example IGST on an intra-state supply, or CGST+SGST
  across states): `block`. This is a frequent and costly error: tax paid under the wrong
  head is not available as credit.
- Place of supply missing: derive from the supplier and buyer GSTIN state codes and show
  `info`.

## 4. Rates
- Valid rates come from a **versioned rate table with effective dates**, keyed on the
  invoice date. Never hard-code rates. As of the September 2025 rate rationalisation
  (effective 22-Sep-2025) the main slabs are 5% and 18%, with 40% for specified goods and
  0%, 0.25% and 3% for special categories; invoices dated earlier may legitimately carry
  12% or 28%.
- A rate not in the table for that invoice date: `warn`.

## 5. Arithmetic
- Per line: quantity × unit price − discount = taxable value; taxable value × rate = tax.
- Totals: sum of lines = subtotal; subtotal + taxes + cess ± round-off = grand total.
- Tolerance: ₹1.00 per comparison (invoices round to the rupee). Over tolerance: `block`.
- Use exact decimal arithmetic. Never floating point for money.

## 6. HSN / SAC
- Each line must carry a code. Missing: `warn`. Fewer than 4 digits: `warn` (suppliers with
  turnover above ₹5 crore must show 6 digits on B2B invoices; we cannot see supplier
  turnover, so we do not enforce 6).

## 7. E-invoicing
- If the invoice shows an IRN (64-character hash) and signed QR code, record the IRN.
- If not, flag `info`: suppliers above the e-invoicing turnover threshold must issue
  e-invoices, and credit on a non-compliant invoice can be denied. We cannot verify supplier
  turnover, so this is never stronger than `info` unless the vendor master marks the
  vendor as e-invoice mandated, in which case it is `warn`.

## 8. Duplicates (GST view)
- An invoice number is unique per supplier GSTIN per financial year (April to March).
  The same supplier GSTIN + normalised invoice number + financial year already on file:
  `block` (exact duplicate).
- Normalise invoice numbers before comparing: uppercase, strip spaces, treat `-` and `/`
  as equal, strip leading zeros from numeric runs.

## Tests
Every rule above needs unit tests with both a passing and a failing case, including a
GSTIN with a wrong check character, IGST on an intra-state supply, and a total that is
off by ₹1.01.
