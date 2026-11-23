"""`IndiaGST`: the India GST tax regime (gst-invoice-rules §1-§7, spec FR-25, FR-26).

Pure and parallel-safe: no DB, no I/O after the seed tables are loaded, no mutable state.
Duplicate checks (§8) live in the duplicate library (slice S3), because they need other invoices.

Flags never reject or approve an invoice; a person decides.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from app.tax.flags import Flag, Severity
from app.tax.gstin import GSTIN_PATTERN, gstin_problem, normalise_gstin, state_codes
from app.tax.invoice import Invoice, TaxHead
from app.tax.rates import RateTable, india_seed_rate_table

TOLERANCE = Decimal("1.00")  # §5: per comparison; invoices round to the rupee
INVOICE_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9/-]{1,16}$")
IRN_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
ZERO = Decimal(0)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _rupees(amount: Decimal) -> str:
    return f"₹{amount:,.2f}"


def _over_tolerance(expected: Decimal, actual: Decimal) -> bool:
    return abs(expected - actual) > TOLERANCE


@dataclass(frozen=True, slots=True)
class _Supply:
    """Where the supply goes from and to. `intra` is None when it cannot be decided."""

    supplier_state: str | None
    place_of_supply: str | None
    intra: bool | None


class IndiaGST:
    """India GST validation for one entity.

    `entity_gstins` are the entity's own GST registrations; an invoice addressed to any other
    GSTIN is flagged (§1). The rate table defaults to the shipped seed table (C5).
    """

    def __init__(self, entity_gstins: Iterable[str], rate_table: RateTable | None = None) -> None:
        self._entity_gstins = frozenset(normalise_gstin(g) for g in entity_gstins)
        self._rate_table = rate_table or india_seed_rate_table()

    @property
    def rate_table(self) -> RateTable:
        return self._rate_table

    def validate(self, invoice: Invoice, *, vendor_e_invoice_mandated: bool = False) -> list[Flag]:
        """Every flag for the invoice, in rule order (§1 to §7)."""
        supply, supply_flags = self._supply(invoice)
        return [
            *self._gstin(invoice),
            *self._mandatory(invoice, supply),
            *supply_flags,
            *self._tax_heads(invoice, supply),
            *self._rates(invoice),
            *self._arithmetic(invoice),
            *self._hsn(invoice),
            *self._e_invoice(invoice, vendor_e_invoice_mandated),
        ]

    # §1 GSTIN ------------------------------------------------------------------------------

    def _gstin(self, inv: Invoice) -> list[Flag]:
        flags: list[Flag] = []
        if _blank(inv.supplier.gstin):
            flags.append(
                Flag(
                    "GST1.SUPPLIER_GSTIN_MISSING",
                    Severity.WARN,
                    "No supplier GSTIN on the invoice: the supplier is unregistered, so no input "
                    "tax credit is available.",
                    "Check the document for a GSTIN. If the supplier is registered, enter it; "
                    "otherwise acknowledge that no credit can be claimed.",
                    "supplier.gstin",
                )
            )
        else:
            gstin = normalise_gstin(inv.supplier.gstin)  # type: ignore[arg-type]
            if problem := gstin_problem(gstin):
                flags.append(
                    Flag(
                        "GST1.SUPPLIER_GSTIN_INVALID",
                        Severity.BLOCK,
                        f"Supplier GSTIN {gstin} is not valid: {problem}.",
                        "Compare the GSTIN with the document and the vendor master and correct it. "
                        "If the invoice itself is wrong, ask the supplier for a corrected invoice.",
                        "supplier.gstin",
                    )
                )
        if not _blank(inv.buyer.gstin):
            gstin = normalise_gstin(inv.buyer.gstin)  # type: ignore[arg-type]
            if problem := gstin_problem(gstin):
                flags.append(
                    Flag(
                        "GST1.BUYER_GSTIN_INVALID",
                        Severity.BLOCK,
                        f"Buyer GSTIN {gstin} is not valid: {problem}.",
                        "Correct the buyer GSTIN from the document. If the invoice shows a wrong "
                        "GSTIN, ask the supplier to reissue it to our registration.",
                        "buyer.gstin",
                    )
                )
            elif gstin not in self._entity_gstins:
                flags.append(
                    Flag(
                        "GST1.BUYER_GSTIN_NOT_OURS",
                        Severity.BLOCK,
                        f"Buyer GSTIN {gstin} is not one of this entity's GST registrations; the "
                        "invoice may be addressed to someone else.",
                        "Check whether the invoice belongs to another entity. If it is ours, ask "
                        "the supplier to reissue it to the correct GSTIN.",
                        "buyer.gstin",
                    )
                )
        return flags

    # §2 Mandatory contents (Rule 46) ------------------------------------------------------

    def _mandatory(self, inv: Invoice, supply: _Supply) -> list[Flag]:
        flags: list[Flag] = []

        def missing(field: str, label: str, severity: Severity) -> None:
            rule = "GST2.MISSING_REQUIRED" if severity is Severity.BLOCK else "GST2.MISSING_FIELD"
            flags.append(
                Flag(
                    rule,
                    severity,
                    f"{label} is missing (required on a tax invoice by Rule 46).",
                    f"Enter the {label.lower()} from the document. If the document does not show "
                    "it, ask the supplier for a corrected invoice.",
                    field,
                )
            )

        for field, label in (
            ("invoice_number", "Invoice number"),
            ("invoice_date", "Invoice date"),
            ("grand_total", "Invoice total"),
        ):
            if _blank(getattr(inv, field)):
                missing(field, label, Severity.BLOCK)
        for field, label in (
            ("supplier.name", "Supplier name"),
            ("supplier.address", "Supplier address"),
            ("buyer.name", "Recipient name"),
            ("buyer.address", "Recipient address"),
            ("buyer.gstin", "Recipient GSTIN"),
        ):
            party, attr = field.split(".")
            if _blank(getattr(getattr(inv, party), attr)):
                missing(field, label, Severity.WARN)
        if _blank(inv.place_of_supply) and supply.intra is False:
            missing("place_of_supply", "Place of supply (inter-state supply)", Severity.WARN)

        if not inv.lines:
            missing("lines", "Line items", Severity.WARN)
        for i, line in enumerate(inv.lines):
            prefix = f"lines[{i}]"
            if _blank(line.description):
                missing(f"{prefix}.description", f"Line {i + 1} description", Severity.WARN)
            if line.quantity is None and not line.is_service:
                missing(f"{prefix}.quantity", f"Line {i + 1} quantity", Severity.WARN)
            if line.taxable_value is None:
                missing(f"{prefix}.taxable_value", f"Line {i + 1} taxable value", Severity.WARN)
            if not line.taxes or any(t.rate is None or t.amount is None for t in line.taxes):
                missing(
                    f"{prefix}.taxes",
                    f"Line {i + 1} tax rate and amount per tax head",
                    Severity.WARN,
                )

        number = inv.invoice_number
        if not _blank(number) and not INVOICE_NUMBER_PATTERN.fullmatch(number.strip()):  # type: ignore[union-attr]
            flags.append(
                Flag(
                    "GST2.INVOICE_NUMBER_FORMAT",
                    Severity.WARN,
                    f"Invoice number '{number}' breaks Rule 46: at most 16 characters, using only "
                    "letters, digits, '-' and '/'.",
                    "Check the number was read correctly. If the supplier's number really breaks "
                    "the rule, acknowledge it or ask for a corrected invoice.",
                    "invoice_number",
                )
            )
        return flags

    # §3 Tax heads and place of supply -----------------------------------------------------

    def _supply(self, inv: Invoice) -> tuple[_Supply, list[Flag]]:
        flags: list[Flag] = []
        codes = state_codes()

        def state_of(gstin: str | None) -> str | None:
            if _blank(gstin):
                return None
            g = normalise_gstin(gstin)  # type: ignore[arg-type]
            return g[:2] if GSTIN_PATTERN.fullmatch(g) and g[:2] in codes else None

        supplier_state = state_of(inv.supplier.gstin)
        pos = None if _blank(inv.place_of_supply) else inv.place_of_supply.strip().zfill(2)  # type: ignore[union-attr]
        if pos is not None and pos not in codes:
            flags.append(
                Flag(
                    "GST3.PLACE_OF_SUPPLY_INVALID",
                    Severity.WARN,
                    f"Place of supply '{inv.place_of_supply}' is not a GST state code.",
                    "Enter the two-digit state code of the place of supply shown on the invoice.",
                    "place_of_supply",
                )
            )
            pos = None
        if pos is None:
            pos = state_of(inv.buyer.gstin)
            if pos is not None:
                flags.append(
                    Flag(
                        "GST3.PLACE_OF_SUPPLY_DERIVED",
                        Severity.INFO,
                        f"Place of supply not shown; taken as {codes[pos].name} ({pos}) from the "
                        "buyer GSTIN.",
                        "No action needed unless the goods or services were supplied elsewhere.",
                        "place_of_supply",
                    )
                )
        intra = None if supplier_state is None or pos is None else supplier_state == pos
        return _Supply(supplier_state, pos, intra), flags

    def _tax_heads(self, inv: Invoice, supply: _Supply) -> list[Flag]:
        if supply.intra is None or supply.place_of_supply is None:
            return []
        state = state_codes()[supply.place_of_supply]
        local_head = TaxHead.UTGST if state.ut_without_legislature else TaxHead.SGST
        expected = {TaxHead.CGST, local_head} if supply.intra else {TaxHead.IGST}
        kind = (
            f"an intra-state supply within {state.name}"
            if supply.intra
            else f"an inter-state supply (supplier state {supply.supplier_state}, place of supply "
            f"{supply.place_of_supply})"
        )
        want = f"CGST + {local_head} at equal halves" if supply.intra else "IGST only"
        flags: list[Flag] = []

        def check(heads: set[TaxHead], field: str, where: str) -> None:
            if heads and heads != expected:
                found = " + ".join(sorted(heads))
                flags.append(
                    Flag(
                        "GST3.WRONG_TAX_HEAD",
                        Severity.BLOCK,
                        f"{where} charges {found} on {kind}, which needs {want}. Tax paid under "
                        "the wrong head is not available as credit.",
                        "Check the place of supply and GSTINs. If they are right, ask the "
                        f"supplier for a corrected invoice charging {want}.",
                        field,
                    )
                )

        for i, line in enumerate(inv.lines):
            charged = {t.head for t in line.taxes if (t.rate or ZERO) or (t.amount or ZERO)}
            check(charged, f"lines[{i}].taxes", f"Line {i + 1}")
            if supply.intra and charged == expected:
                rates = {t.head: t.rate for t in line.taxes if t.head in expected}
                if None not in rates.values() and len(set(rates.values())) > 1:
                    flags.append(
                        Flag(
                            "GST3.UNEQUAL_HALVES",
                            Severity.BLOCK,
                            f"Line {i + 1}: CGST {rates[TaxHead.CGST]}% and {local_head} "
                            f"{rates[local_head]}% differ; each must be half the total rate.",
                            "Check the rates read from the document. If the invoice is wrong, ask "
                            "the supplier for a corrected invoice.",
                            f"lines[{i}].taxes",
                        )
                    )
        check({h for h, amt in inv.tax_totals if amt}, "tax_totals", "The tax summary")
        return flags

    # §4 Rates ------------------------------------------------------------------------------

    def _rates(self, inv: Invoice) -> list[Flag]:
        if inv.invoice_date is None:
            return []
        flags: list[Flag] = []
        for i, line in enumerate(inv.lines):
            if not line.taxes or any(t.rate is None for t in line.taxes):
                continue
            rate = sum((t.rate for t in line.taxes), ZERO)  # type: ignore[misc]
            if not self._rate_table.is_known_rate(rate, inv.invoice_date):
                known = ", ".join(
                    f"{r.normalize():f}%"
                    for r in sorted(self._rate_table.rates_on(inv.invoice_date))
                )
                flags.append(
                    Flag(
                        "GST4.UNKNOWN_RATE",
                        Severity.WARN,
                        f"Line {i + 1}: total GST rate {rate.normalize():f}% is not in the rate "
                        f"table for {inv.invoice_date:%d-%b-%Y} (known: {known or 'none'}).",
                        "Check the rate was read correctly and is right for this HSN/SAC code on "
                        "the invoice date. If the rate table is out of date, ask an admin to "
                        "update it.",
                        f"lines[{i}].taxes",
                    )
                )
        return flags

    # §5 Arithmetic -------------------------------------------------------------------------

    def _arithmetic(self, inv: Invoice) -> list[Flag]:
        flags: list[Flag] = []

        def mismatch(rule: str, field: str, what: str, expected: Decimal, shown: Decimal) -> None:
            if _over_tolerance(expected, shown):
                flags.append(
                    Flag(
                        rule,
                        Severity.BLOCK,
                        f"{what}: shown {_rupees(shown)}, calculated {_rupees(expected)} (off by "
                        f"{_rupees(abs(expected - shown))}; tolerance {_rupees(TOLERANCE)}).",
                        "Check the figures were read correctly from the document. If the invoice "
                        "itself does not add up, ask the supplier for a corrected invoice.",
                        field,
                    )
                )

        for i, line in enumerate(inv.lines):
            n = i + 1
            if None not in (line.quantity, line.unit_price, line.taxable_value):
                expected = line.quantity * line.unit_price - (line.discount or ZERO)  # type: ignore[operator]
                mismatch(
                    "GST5.LINE_TAXABLE_VALUE",
                    f"lines[{i}].taxable_value",
                    f"Line {n} taxable value (quantity × unit price − discount)",
                    expected,
                    line.taxable_value,  # type: ignore[arg-type]
                )
            if line.taxable_value is not None:
                for t in line.taxes:
                    if t.rate is not None and t.amount is not None:
                        mismatch(
                            "GST5.LINE_TAX_AMOUNT",
                            f"lines[{i}].taxes",
                            f"Line {n} {t.head} at {t.rate.normalize():f}%",
                            line.taxable_value * t.rate / 100,
                            t.amount,
                        )

        line_subtotal = (
            sum((ln.taxable_value for ln in inv.lines), ZERO)  # type: ignore[misc]
            if inv.lines and all(ln.taxable_value is not None for ln in inv.lines)
            else None
        )
        if inv.subtotal is not None and line_subtotal is not None:
            mismatch(
                "GST5.SUBTOTAL", "subtotal", "Subtotal (sum of lines)", line_subtotal, inv.subtotal
            )

        all_taxes = [t for ln in inv.lines for t in ln.taxes]
        line_taxes: dict[TaxHead, Decimal] | None = None
        if inv.lines and all(t.amount is not None for t in all_taxes):
            line_taxes = {}
            for t in all_taxes:
                line_taxes[t.head] = line_taxes.get(t.head, ZERO) + t.amount  # type: ignore[operator]
        summary = dict(inv.tax_totals)
        if summary and line_taxes is not None:
            for head in sorted(set(summary) | set(line_taxes)):
                mismatch(
                    "GST5.TAX_TOTAL",
                    "tax_totals",
                    f"{head} total (sum of lines)",
                    line_taxes.get(head, ZERO),
                    summary.get(head, ZERO),
                )

        base = inv.subtotal if inv.subtotal is not None else line_subtotal
        taxes = summary or line_taxes
        cess = inv.cess_total
        if cess is None and inv.lines and all(ln.cess is not None for ln in inv.lines):
            cess = sum((ln.cess for ln in inv.lines), ZERO)  # type: ignore[misc]
        if inv.grand_total is not None and base is not None and taxes is not None:
            expected = base + sum(taxes.values(), ZERO) + (cess or ZERO) + (inv.round_off or ZERO)
            mismatch(
                "GST5.GRAND_TOTAL",
                "grand_total",
                "Invoice total (subtotal + taxes + cess ± round-off)",
                expected,
                inv.grand_total,
            )
        return flags

    # §6 HSN / SAC --------------------------------------------------------------------------

    def _hsn(self, inv: Invoice) -> list[Flag]:
        flags: list[Flag] = []
        for i, line in enumerate(inv.lines):
            field = f"lines[{i}].hsn_sac"
            if _blank(line.hsn_sac):
                flags.append(
                    Flag(
                        "GST6.HSN_MISSING",
                        Severity.WARN,
                        f"Line {i + 1} has no HSN/SAC code.",
                        "Enter the code from the document. If the invoice has none, ask the "
                        "supplier to show it.",
                        field,
                    )
                )
                continue
            code = line.hsn_sac.replace(" ", "").replace(".", "")  # type: ignore[union-attr]
            if not code.isdigit() or len(code) < 4:
                flags.append(
                    Flag(
                        "GST6.HSN_TOO_SHORT",
                        Severity.WARN,
                        f"Line {i + 1} HSN/SAC code '{line.hsn_sac}' is not at least 4 digits.",
                        "Check the code was read correctly. If the invoice shows fewer than 4 "
                        "digits, ask the supplier to show the full code.",
                        field,
                    )
                )
        return flags

    # §7 E-invoicing ------------------------------------------------------------------------

    def _e_invoice(self, inv: Invoice, mandated: bool) -> list[Flag]:
        irn_ok = not _blank(inv.irn) and IRN_PATTERN.fullmatch(inv.irn.strip())  # type: ignore[union-attr]
        if irn_ok and inv.has_signed_qr:
            return []
        if _blank(inv.irn):
            found = "no IRN"
        elif not irn_ok:
            found = "an IRN that is not a 64-character hash"
        else:
            found = "an IRN but no signed QR code"
        return [
            Flag(
                "GST7.NOT_E_INVOICE",
                Severity.WARN if mandated else Severity.INFO,
                f"The invoice shows {found}. Suppliers above the e-invoicing threshold must issue "
                "e-invoices, and credit on a non-compliant invoice can be denied.",
                "Ask the supplier for the e-invoice (IRN and signed QR code)."
                if mandated
                else "No action needed unless you know this supplier must issue e-invoices.",
                "irn",
            )
        ]
