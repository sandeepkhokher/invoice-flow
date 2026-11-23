"""`ExtractedInvoice`: the fixed schema the model fills (spec FR-20, §5.3, data-protection §4).

The model only ever fills this schema; its output drives no action. Every field carries the
value as printed (or null), the model's own confidence and the page it was found on (FR-21).
Amounts and dates are strings in a stated format so no float reaches money code: the
deterministic checks parse them with `Decimal` and lower the confidence when they do not parse.

`flatten()` turns an extraction into named fields (`supplier.gstin`, `lines[0].hsn_sac`,
`tax_totals.cgst`), the names stored in `invoice_fields` and used by the review screen. Names
match the `field` of the tax layer's flags where both exist.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
CONFIDENCE_LEVELS: tuple[Confidence, ...] = ("low", "medium", "high")

_PAGE = "1-based page number the value was read from; null when the value is null."
_CONF = (
    "high: printed clearly and read with certainty. medium: legible but some doubt "
    "(smudged, ambiguous layout). low: guessed from context or barely legible."
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TextField(_Strict):
    """A text value exactly as printed."""

    value: str | None = Field(description="The text as printed, or null if not printed.")
    confidence: Confidence = Field(description=_CONF)
    page: int | None = Field(description=_PAGE)


class AmountField(_Strict):
    """A number as printed, written as a plain decimal string."""

    value: str | None = Field(
        description=(
            "Plain decimal with a dot and no currency symbol, commas or spaces, for example "
            "125000.00 or 18 or -0.40. Null if not printed."
        )
    )
    confidence: Confidence = Field(description=_CONF)
    page: int | None = Field(description=_PAGE)


class DateField(_Strict):
    """A date as printed, rewritten in ISO form."""

    value: str | None = Field(
        description="The printed date as YYYY-MM-DD (Indian invoices print day first). Null "
        "if not printed."
    )
    confidence: Confidence = Field(description=_CONF)
    page: int | None = Field(description=_PAGE)


class FlagField(_Strict):
    """A yes/no fact about the document."""

    value: bool | None = Field(description="true or false, or null if it cannot be told.")
    confidence: Confidence = Field(description=_CONF)
    page: int | None = Field(description=_PAGE)


class PartyBlock(_Strict):
    name: TextField
    address: TextField = Field(description="Full postal address as printed, one line.")
    gstin: TextField = Field(description="15-character GSTIN as printed.")


class LineItem(_Strict):
    description: TextField
    hsn_sac: TextField = Field(description="HSN or SAC code as printed (digits).")
    quantity: AmountField
    unit_price: AmountField
    discount: AmountField = Field(description="Discount amount in rupees; null if none.")
    taxable_value: AmountField
    rate: AmountField = Field(
        description="Total GST rate in percent for the line (CGST + SGST, or IGST), e.g. 18."
    )
    cgst: AmountField = Field(description="CGST amount in rupees on this line; null if none.")
    sgst: AmountField = Field(description="SGST amount in rupees on this line; null if none.")
    utgst: AmountField = Field(description="UTGST amount in rupees on this line; null if none.")
    igst: AmountField = Field(description="IGST amount in rupees on this line; null if none.")
    cess: AmountField = Field(description="Cess amount in rupees on this line; null if none.")


class TaxSummary(_Strict):
    subtotal: AmountField = Field(description="Total taxable value (sum of the lines).")
    cgst: AmountField
    sgst: AmountField
    utgst: AmountField
    igst: AmountField
    cess: AmountField
    round_off: AmountField = Field(description="Round-off adjustment, signed; null if none.")
    grand_total: AmountField = Field(description="Invoice total payable.")


class BankBlock(_Strict):
    account_name: TextField
    account_number: TextField
    ifsc: TextField
    bank_name: TextField


class ExtractedInvoice(_Strict):
    """Everything FR-20 names: header, parties, lines, tax summary, bank details and IRN."""

    invoice_number: TextField
    invoice_date: DateField
    supplier: PartyBlock = Field(description="The seller who issued the invoice.")
    buyer: PartyBlock = Field(description="The recipient (bill-to) of the invoice.")
    place_of_supply: TextField = Field(
        description="Two-digit GST state code of the place of supply, e.g. 27. Null if not printed."
    )
    reverse_charge: FlagField = Field(description="Whether tax is payable on reverse charge.")
    line_items: list[LineItem]
    tax_summary: TaxSummary
    bank: BankBlock = Field(description="The supplier's bank details printed for payment.")
    irn: TextField = Field(description="E-invoice IRN (64 hex characters) if printed.")
    has_signed_qr: FlagField = Field(description="Whether an e-invoice QR code is printed.")


# --- named fields ---------------------------------------------------------------------------

# Restricted (data-protection §1): never stored or shown in full by S9 (see `app/extraction`).
RESTRICTED_FIELDS = frozenset({"bank.account_number", "bank.ifsc"})

FieldValue = TextField | AmountField | DateField | FlagField


def flatten(inv: ExtractedInvoice) -> dict[str, FieldValue]:
    """Named fields in a stable order: header, parties, lines, tax summary, bank, e-invoice."""
    out: dict[str, FieldValue] = {
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date,
    }
    for party in ("supplier", "buyer"):
        block: PartyBlock = getattr(inv, party)
        for attr in ("name", "address", "gstin"):
            out[f"{party}.{attr}"] = getattr(block, attr)
    out["place_of_supply"] = inv.place_of_supply
    out["reverse_charge"] = inv.reverse_charge
    for i, line in enumerate(inv.line_items):
        for attr in LineItem.model_fields:
            out[f"lines[{i}].{attr}"] = getattr(line, attr)
    ts = inv.tax_summary
    out["subtotal"] = ts.subtotal
    for head in ("cgst", "sgst", "utgst", "igst"):
        out[f"tax_totals.{head}"] = getattr(ts, head)
    out["cess_total"] = ts.cess
    out["round_off"] = ts.round_off
    out["grand_total"] = ts.grand_total
    for attr in BankBlock.model_fields:
        out[f"bank.{attr}"] = getattr(inv.bank, attr)
    out["irn"] = inv.irn
    out["has_signed_qr"] = inv.has_signed_qr
    return out
