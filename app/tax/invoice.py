"""The extracted invoice as the tax layer sees it.

Plain immutable values, no DB and no I/O. The extraction slice (S9) maps its schema onto these.
Every field may be missing (`None`), because extraction may not find it; the rules flag that.
Money and rates are `Decimal`; floats are rejected so they cannot creep in.
"""

from dataclasses import dataclass, fields
from datetime import date
from decimal import Decimal
from enum import StrEnum


class TaxHead(StrEnum):
    CGST = "CGST"
    SGST = "SGST"
    UTGST = "UTGST"
    IGST = "IGST"


def _reject_floats(obj: object) -> None:
    for f in fields(obj):  # type: ignore[arg-type]
        if isinstance(getattr(obj, f.name), float):
            raise TypeError(f"{type(obj).__name__}.{f.name} must be Decimal, not float")


@dataclass(frozen=True, slots=True)
class Party:
    name: str | None = None
    address: str | None = None
    gstin: str | None = None


@dataclass(frozen=True, slots=True)
class LineTax:
    """One tax head on a line: rate in percent (9 means 9%) and amount in rupees."""

    head: TaxHead
    rate: Decimal | None = None
    amount: Decimal | None = None

    def __post_init__(self) -> None:
        _reject_floats(self)


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    description: str | None = None
    hsn_sac: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    discount: Decimal | None = None
    taxable_value: Decimal | None = None
    taxes: tuple[LineTax, ...] = ()
    cess: Decimal | None = None

    def __post_init__(self) -> None:
        _reject_floats(self)

    @property
    def is_service(self) -> bool:
        """SAC codes (services) start with 99; everything else is goods."""
        return (self.hsn_sac or "").strip().startswith("99")


@dataclass(frozen=True, slots=True)
class Invoice:
    invoice_number: str | None = None
    invoice_date: date | None = None
    supplier: Party = Party()
    buyer: Party = Party()
    place_of_supply: str | None = None  # two-digit state code, e.g. "27"
    reverse_charge: bool | None = None
    lines: tuple[InvoiceLine, ...] = ()
    subtotal: Decimal | None = None
    # Invoice-level tax summary per head. Empty means "not shown"; totals then use the line sums.
    tax_totals: tuple[tuple[TaxHead, Decimal], ...] = ()
    cess_total: Decimal | None = None
    round_off: Decimal | None = None
    grand_total: Decimal | None = None
    irn: str | None = None
    has_signed_qr: bool = False

    def __post_init__(self) -> None:
        _reject_floats(self)
        for _, amount in self.tax_totals:
            if isinstance(amount, float):
                raise TypeError("Invoice.tax_totals amounts must be Decimal, not float")
