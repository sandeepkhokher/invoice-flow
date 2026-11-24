"""Synthetic extractions for S9 tests (data-protection §7: nothing real).

`clean()` is a consistent intra-state invoice from a supplier in Maharashtra (27) to our
registration `BUYER_GSTIN` (also 27): one line, 10 × ₹1,000 at 18% (CGST 9% + SGST 9%),
total ₹11,800, with an IRN and QR. Every field is `high`.
"""

from __future__ import annotations

from typing import Any

from app.extraction.schema import ExtractedInvoice
from app.tax.gstin import check_character


def gstin_for(state: str, pan: str) -> str:
    first14 = f"{state}{pan}1Z"
    return first14 + check_character(first14)


BUYER_GSTIN = gstin_for("27", "ZXCPA0001A")  # entity 1's registration in the tests
SUPPLIER_GSTIN = gstin_for("27", "ZXSPP1234Q")
IRN = "a" * 64
ACCOUNT_NUMBER = "001234567890"


def f(value: Any, confidence: str = "high", page: int | None = 1) -> dict[str, Any]:
    return {"value": value, "confidence": confidence, "page": page if value is not None else None}


def clean_dict(**top: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "invoice_number": f("INV-0042"),
        "invoice_date": f("2026-11-01"),
        "supplier": {
            "name": f("Tamarind Traders Pvt Ltd"),
            "address": f("12 MG Road, Pune 411001"),
            "gstin": f(SUPPLIER_GSTIN),
        },
        "buyer": {
            "name": f("Entity One Pvt Ltd"),
            "address": f("Plot 1, Pune 411019"),
            "gstin": f(BUYER_GSTIN),
        },
        "place_of_supply": f("27"),
        "reverse_charge": f(False),
        "line_items": [
            {
                "description": f("Steel brackets"),
                "hsn_sac": f("73269099"),
                "quantity": f("10"),
                "unit_price": f("1000.00"),
                "discount": f("0.00"),
                "taxable_value": f("10000.00"),
                "rate": f("18"),
                "cgst": f("900.00"),
                "sgst": f("900.00"),
                "utgst": f(None),
                "igst": f(None),
                "cess": f(None),
            }
        ],
        "tax_summary": {
            "subtotal": f("10000.00"),
            "cgst": f("900.00"),
            "sgst": f("900.00"),
            "utgst": f(None),
            "igst": f(None),
            "cess": f(None),
            "round_off": f(None),
            "grand_total": f("11800.00"),
        },
        "bank": {
            "account_name": f("Tamarind Traders Pvt Ltd"),
            "account_number": f(ACCOUNT_NUMBER),
            "ifsc": f("ZYBK0001234"),
            "bank_name": f("Zylo Bank"),
        },
        "irn": f(IRN),
        "has_signed_qr": f(True),
    }
    data.update(top)
    return data


def clean(**top: Any) -> ExtractedInvoice:
    return ExtractedInvoice.model_validate(clean_dict(**top))


def with_total(total: str, confidence: str = "high") -> ExtractedInvoice:
    data = clean_dict()
    data["tax_summary"]["grand_total"] = f(total, confidence)
    return ExtractedInvoice.model_validate(data)


# Text a digital PDF of `clean()` would carry (Indian digit grouping, day-first date).
CLEAN_TEXT_LINES = (
    "TAX INVOICE",
    "Tamarind Traders Pvt Ltd",
    f"GSTIN: {SUPPLIER_GSTIN}",
    "Invoice No: INV-0042    Date: 01-11-2026",
    f"Bill to: Entity One Pvt Ltd  GSTIN: {BUYER_GSTIN}",
    "Steel brackets 73269099 10 x 1,000.00 = 10,000.00",
    "Taxable 10,000.00  CGST 9% 900.00  SGST 9% 900.00",
    "Total Rs 11,800.00",
    f"IRN: {IRN}",
)
