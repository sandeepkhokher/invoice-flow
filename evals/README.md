# Evals

## Synthetic eval set (slice S4)
`make gen-invoices` writes 61 synthetic Indian GST invoices to `evals/data/` (git-ignored;
regenerate at any time, the output is byte-identical for the fixed seed):

- `inv-NNN.{pdf,png,jpg}`: the document. Five layouts (`classic`, `modern`, `compact`,
  `ledger`, `letterhead`); digital PDFs with a text layer (some multi-page), and scanned-style
  PDFs and images (rotation, blur, noise, specks, JPEG artefacts).
- `inv-NNN.json`: ground truth, meaning what is printed on the document. `named_fields` holds
  the seven fields from intent success measure 1. Money is a string with two decimals.
- `manifest.json`: our entities and their GSTINs, the `clean` / `duplicates` / `seeded_errors`
  subsets, and per invoice the flags the pipeline is expected to raise.

Seeded duplicates (16): `exact` (same file), `rescan`, `renumbered` (`INV-0042` vs `INV/42`),
`same_amount_date` and `cross_entity`. Each original has a lower id, so ingest in id order.
Seeded errors (9): `wrong_tax_head`, `bad_gstin`, `arithmetic_off`.

Everything is fictitious (data-protection §7): PANs start with `ZX`, GSTINs have valid
checksums, banks use made-up IFSC codes, and every page says it is a synthetic sample.
Options: `uv run python -m evals.synth --out DIR --seed N`.
