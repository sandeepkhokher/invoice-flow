"""Invoice model for the generator, its tax arithmetic, and the ground-truth JSON shape.

Money is ``Decimal`` throughout and serialised as a string with two decimals (never float).
Ground truth records what is *printed* on the document: for a seeded error, the wrong
value is the truth an extractor should return, and ``seeded_error`` says what is wrong.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from evals.synth.catalog import STATES

PAISA = Decimal("0.01")
RUPEE = Decimal("1")

# How tax is printed on the invoice.
CGST_SGST = "cgst_sgst"
CGST_UTGST = "cgst_utgst"
IGST = "igst"


def q2(value: Decimal) -> Decimal:
    return value.quantize(PAISA, rounding=ROUND_HALF_UP)


def money(value: Decimal) -> str:
    return str(q2(value))


def financial_year(d: date) -> str:
    """Indian financial year (April to March) as ``2025-26``."""
    start = d.year if d.month >= 4 else d.year - 1
    return f"{start}-{(start + 1) % 100:02d}"


def normalise_number(number: str) -> str:
    """gst-invoice-rules §8: uppercase, strip spaces, `-` == `/`, strip leading zeros."""
    out, digits = [], ""
    for ch in number.upper().replace(" ", "").replace("/", "-") + "\0":
        if ch.isdigit():
            digits += ch
            continue
        if digits:
            out.append(digits.lstrip("0") or "0")
            digits = ""
        if ch != "\0":
            out.append(ch)
    return "".join(out)


def edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def tax_mode(supplier_state: str, place_of_supply: str) -> str:
    """The correct tax heads for a supply (gst-invoice-rules §3)."""
    if supplier_state != place_of_supply:
        return IGST
    return CGST_UTGST if STATES[place_of_supply][1] else CGST_SGST


@dataclass
class Party:
    name: str
    address: list[str]
    gstin: str
    state_code: str
    pan: str
    email: str = ""
    entity: str | None = None  # our entity key, for buyers

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "address": ", ".join(self.address),
            "gstin": self.gstin,
            "state_code": self.state_code,
            "state": STATES[self.state_code][0],
        }


@dataclass
class Bank:
    bank_name: str
    account_name: str
    account_number: str
    ifsc: str
    branch: str

    def to_json(self) -> dict:
        return {
            "bank_name": self.bank_name,
            "account_name": self.account_name,
            "account_number": self.account_number,
            "ifsc": self.ifsc,
        }


@dataclass
class Line:
    description: str
    hsn: str
    quantity: Decimal
    unit: str
    unit_price: Decimal
    discount: Decimal
    rate: Decimal
    taxable_value: Decimal = PAISA * 0
    cgst: Decimal = PAISA * 0
    sgst: Decimal = PAISA * 0
    utgst: Decimal = PAISA * 0
    igst: Decimal = PAISA * 0

    @property
    def tax(self) -> Decimal:
        return self.cgst + self.sgst + self.utgst + self.igst

    def to_json(self) -> dict:
        return {
            "description": self.description,
            "hsn": self.hsn,
            "quantity": str(self.quantity),
            "unit": self.unit,
            "unit_price": money(self.unit_price),
            "discount": money(self.discount),
            "taxable_value": money(self.taxable_value),
            "rate": str(self.rate),
            "cgst": money(self.cgst),
            "sgst": money(self.sgst),
            "utgst": money(self.utgst),
            "igst": money(self.igst),
        }


@dataclass
class Invoice:
    id: str
    supplier: Party
    buyer: Party
    number: str
    date: date
    lines: list[Line]
    bank: Bank
    layout: str
    fmt: str  # pdf_digital | pdf_scanned | png | jpeg
    rounds: bool = True  # print a round-off line to the nearest rupee
    printed_mode: str = ""  # tax heads as printed; differs from correct mode for wrong_tax_head
    e_invoice: bool = False
    ack_no: str = ""
    seeded_error: str | None = None
    error_detail: dict = field(default_factory=dict)
    duplicate_of: str | None = None
    duplicate_kind: str | None = None
    # computed by compute()
    taxable_total: Decimal = Decimal(0)
    cgst_total: Decimal = Decimal(0)
    sgst_total: Decimal = Decimal(0)
    utgst_total: Decimal = Decimal(0)
    igst_total: Decimal = Decimal(0)
    round_off: Decimal = Decimal(0)
    grand_total: Decimal = Decimal(0)
    scan: dict | None = None
    pages: int = 1
    file: str = ""
    sha256: str = ""

    @property
    def place_of_supply(self) -> str:
        return self.buyer.state_code

    @property
    def correct_mode(self) -> str:
        return tax_mode(self.supplier.state_code, self.place_of_supply)

    @property
    def supply_type(self) -> str:
        return "inter" if self.correct_mode == IGST else "intra"

    @property
    def fy(self) -> str:
        return financial_year(self.date)

    @property
    def irn(self) -> str | None:
        """IRN = SHA-256 of supplier GSTIN + FY + document type + number (64 hex chars)."""
        if not self.e_invoice:
            return None
        raw = f"{self.supplier.gstin}{self.fy}INV{self.number}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def qr_payload(self) -> str | None:
        if not self.e_invoice:
            return None
        return json.dumps(
            {
                "SellerGstin": self.supplier.gstin,
                "BuyerGstin": self.buyer.gstin,
                "DocNo": self.number,
                "DocTyp": "INV",
                "DocDt": self.date.strftime("%d/%m/%Y"),
                "TotInvVal": money(self.grand_total),
                "ItemCnt": len(self.lines),
                "MainHsnCode": self.lines[0].hsn,
                "Irn": self.irn,
            },
            separators=(",", ":"),
        )

    @property
    def tax_total(self) -> Decimal:
        return self.cgst_total + self.sgst_total + self.utgst_total + self.igst_total

    def compute(self) -> None:
        """Compute line and invoice totals from quantities, prices and the printed tax mode."""
        mode = self.printed_mode or self.correct_mode
        self.printed_mode = mode
        for ln in self.lines:
            ln.taxable_value = q2(ln.quantity * ln.unit_price - ln.discount)
            ln.cgst = ln.sgst = ln.utgst = ln.igst = Decimal("0.00")
            if mode == IGST:
                ln.igst = q2(ln.taxable_value * ln.rate / 100)
            else:
                half = q2(ln.taxable_value * ln.rate / 200)
                ln.cgst = half
                if mode == CGST_UTGST:
                    ln.utgst = half
                else:
                    ln.sgst = half
        self.taxable_total = sum((ln.taxable_value for ln in self.lines), Decimal("0.00"))
        self.cgst_total = sum((ln.cgst for ln in self.lines), Decimal("0.00"))
        self.sgst_total = sum((ln.sgst for ln in self.lines), Decimal("0.00"))
        self.utgst_total = sum((ln.utgst for ln in self.lines), Decimal("0.00"))
        self.igst_total = sum((ln.igst for ln in self.lines), Decimal("0.00"))
        raw = self.taxable_total + self.tax_total
        if self.rounds:
            self.grand_total = raw.quantize(RUPEE, rounding=ROUND_HALF_UP).quantize(PAISA)
        else:
            self.grand_total = raw
        self.round_off = self.grand_total - raw

    def ground_truth(self) -> dict:
        return {
            "id": self.id,
            "file": self.file,
            "sha256": self.sha256,
            "format": self.fmt,
            "layout": self.layout,
            "pages": self.pages,
            "scan": self.scan,
            # The seven fields named in intent success measure 1.
            "named_fields": {
                "vendor": self.supplier.name,
                "supplier_gstin": self.supplier.gstin,
                "invoice_number": self.number,
                "invoice_date": self.date.isoformat(),
                "subtotal": money(self.taxable_total),
                "tax_amounts": {
                    "cgst": money(self.cgst_total),
                    "sgst": money(self.sgst_total),
                    "utgst": money(self.utgst_total),
                    "igst": money(self.igst_total),
                },
                "total": money(self.grand_total),
            },
            "invoice_number": self.number,
            "invoice_date": self.date.isoformat(),
            "financial_year": self.fy,
            "currency": "INR",
            "supplier": self.supplier.to_json(),
            "buyer": self.buyer.to_json(),
            "buyer_entity": self.buyer.entity,
            "place_of_supply": {
                "state_code": self.place_of_supply,
                "state": STATES[self.place_of_supply][0],
            },
            "supply_type": self.supply_type,
            "tax_heads_printed": self.printed_mode,
            "reverse_charge": False,
            "line_items": [ln.to_json() for ln in self.lines],
            "tax_summary": {
                "taxable_total": money(self.taxable_total),
                "cgst": money(self.cgst_total),
                "sgst": money(self.sgst_total),
                "utgst": money(self.utgst_total),
                "igst": money(self.igst_total),
                "cess": "0.00",
                "round_off": money(self.round_off),
                "grand_total": money(self.grand_total),
            },
            "irn": self.irn,
            "ack_no": self.ack_no or None,
            "has_qr": self.e_invoice,
            "qr_payload": self.qr_payload,
            "bank": self.bank.to_json(),
            "seeded_error": (
                {"kind": self.seeded_error, **self.error_detail} if self.seeded_error else None
            ),
            "duplicate": (
                {"of": self.duplicate_of, "kind": self.duplicate_kind}
                if self.duplicate_of
                else None
            ),
            "synthetic": True,
        }
