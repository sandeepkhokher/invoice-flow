"""Confidence merge and deterministic checks (FR-21, spec §5.3).

Confidence = the model's rating, lowered one level when a check fails: value absent from the
PDF text layer, GSTIN checksum, arithmetic, date in the future or over 18 months old.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.extraction.checks import (
    lower,
    parse_amount,
    pdf_text_layer,
    score,
    squash,
    to_tax_invoice,
)
from app.extraction.schema import ExtractedInvoice
from app.tax import IndiaGST, TaxHead
from tests.extraction.factory import (
    BUYER_GSTIN,
    CLEAN_TEXT_LINES,
    clean,
    clean_dict,
    f,
    with_total,
)
from tests.intake import samples

TODAY = date(2026, 11, 24)


def scored(inv: ExtractedInvoice, text: str | None = None, today: date = TODAY) -> dict:
    flags = IndiaGST([BUYER_GSTIN]).validate(to_tax_invoice(inv))
    return {s.name: s for s in score(inv, tax_flags=flags, text_layer=text, today=today)}


def clean_text() -> str:
    return pdf_text_layer(samples.text_pdf(*CLEAN_TEXT_LINES))


def test_clean_invoice_keeps_every_field_high_and_has_no_validation_flags():
    inv = clean()
    assert IndiaGST([BUYER_GSTIN]).validate(to_tax_invoice(inv)) == []
    fields = scored(inv, clean_text())
    assert fields and all(s.confidence == "high" for s in fields.values())
    assert all(s.failed_checks == () for s in fields.values())


def test_lower_goes_down_one_level_and_stops_at_low():
    assert (lower("high"), lower("medium"), lower("low")) == ("medium", "low", "low")


def test_value_absent_from_the_text_layer_is_lowered():
    data = clean_dict(invoice_number=f("INV-0043"))  # the PDF says INV-0042
    s = scored(ExtractedInvoice.model_validate(data), clean_text())["invoice_number"]
    assert (s.model_confidence, s.confidence) == ("high", "medium")
    assert s.failed_checks == ("not_in_text_layer",)


def test_amounts_and_dates_match_their_printed_forms_in_the_text_layer():
    fields = scored(clean(), clean_text())
    for name in ("grand_total", "subtotal", "tax_totals.cgst", "invoice_date"):
        assert fields[name].failed_checks == (), name


def test_no_text_layer_skips_the_text_check():
    assert pdf_text_layer(samples.pdf()) is None  # a scan-like PDF: no extractable text
    data = clean_dict(invoice_number=f("ANYTHING-1"))
    assert scored(ExtractedInvoice.model_validate(data), None)["invoice_number"].confidence == (
        "high"
    )


def test_bad_gstin_checksum_is_lowered():
    bad = BUYER_GSTIN[:-1] + ("A" if BUYER_GSTIN[-1] != "A" else "B")
    data = clean_dict(buyer={**clean_dict()["buyer"], "gstin": f(bad, "medium")})
    s = scored(ExtractedInvoice.model_validate(data))["buyer.gstin"]
    assert (s.model_confidence, s.confidence) == ("medium", "low")
    assert "gstin_checksum" in s.failed_checks


def test_arithmetic_that_does_not_reconcile_lowers_the_total():
    s = scored(with_total("11801.01"))["grand_total"]  # off by ₹1.01, over tolerance
    assert s.confidence == "medium" and "arithmetic" in s.failed_checks
    assert scored(with_total("11801.00"))["grand_total"].confidence == "high"  # within ₹1


def test_future_and_stale_dates_are_lowered():
    future = ExtractedInvoice.model_validate(clean_dict(invoice_date=f("2026-12-01")))
    assert scored(future)["invoice_date"].failed_checks == ("date_in_future",)
    stale = ExtractedInvoice.model_validate(clean_dict(invoice_date=f("2025-05-23")))
    assert scored(stale)["invoice_date"].failed_checks == ("date_too_old",)
    assert scored(stale)["invoice_date"].confidence == "medium"
    edge = ExtractedInvoice.model_validate(clean_dict(invoice_date=f("2025-05-24")))
    assert scored(edge)["invoice_date"].confidence == "high"  # exactly 18 months


def test_several_failed_checks_still_lower_only_one_level():
    data = clean_dict()
    data["tax_summary"]["grand_total"] = f("99999.00")  # wrong sum and not in the text
    s = scored(ExtractedInvoice.model_validate(data), clean_text())["grand_total"]
    assert set(s.failed_checks) == {"arithmetic", "not_in_text_layer"}
    assert s.confidence == "medium"


def test_unparsable_numbers_and_dates_become_low():
    data = clean_dict(invoice_date=f("1st Nov"))
    data["tax_summary"]["grand_total"] = f("eleven thousand")
    fields = scored(ExtractedInvoice.model_validate(data))
    assert fields["invoice_date"].confidence == "low"
    assert fields["grand_total"].confidence == "low"


def test_parse_amount_is_decimal_and_tolerates_grouping():
    assert parse_amount("1,25,000.50") == Decimal("125000.50")
    assert parse_amount("₹ 900") == Decimal("900")
    assert parse_amount("NaN") is None and parse_amount("") is None and parse_amount(None) is None


def test_mapping_to_the_tax_layer_splits_the_rate_across_heads():
    tax = to_tax_invoice(clean())
    (line,) = tax.lines
    assert {(t.head, t.rate, t.amount) for t in line.taxes} == {
        (TaxHead.CGST, Decimal(9), Decimal("900.00")),
        (TaxHead.SGST, Decimal(9), Decimal("900.00")),
    }
    assert dict(tax.tax_totals) == {
        TaxHead.CGST: Decimal("900.00"),
        TaxHead.SGST: Decimal("900.00"),
    }
    assert tax.grand_total == Decimal("11800.00") and tax.invoice_date == date(2026, 11, 1)


def test_squash_ignores_layout():
    assert squash("Rs 1,25,000.00\n GSTIN 27 abc") == "RS125000.00GSTIN27ABC"
