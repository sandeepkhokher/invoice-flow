"""IndiaGST: a passing and a failing case for every rule in gst-invoice-rules §1-§7."""

from dataclasses import replace
from datetime import date
from decimal import Decimal as D

import pytest

from app.tax import (
    Flag,
    IndiaGST,
    Invoice,
    InvoiceLine,
    LineTax,
    Party,
    Severity,
    TaxHead,
    TaxRegime,
)

SUPPLIER_MH = "27AAPFU0939F1ZV"  # Maharashtra (from the ticket)
BUYER_KA = "29AAGCB7383J1Z4"  # Karnataka (from the ticket)
BUYER_MH = "27AAGCB7383J1Z8"  # Maharashtra, same PAN as BUYER_KA
SUPPLIER_CH = "04AAPFU0939F1Z3"  # Chandigarh: UT without legislature
BUYER_CH = "04AAGCB7383J1ZG"
OUR_GSTINS = [BUYER_KA, BUYER_MH, BUYER_CH]
IRN = "a" * 64


def intra_line(**kw) -> InvoiceLine:
    base = InvoiceLine(
        description="Laptop",
        hsn_sac="84713010",
        quantity=D("10"),
        unit_price=D("100.00"),
        taxable_value=D("1000.00"),
        taxes=(
            LineTax(TaxHead.CGST, D("9"), D("90.00")),
            LineTax(TaxHead.SGST, D("9"), D("90.00")),
        ),
    )
    return replace(base, **kw)


def intra_invoice(**kw) -> Invoice:
    """A clean intra-state Maharashtra invoice at 18% after the Sept 2025 rationalisation."""
    base = Invoice(
        invoice_number="INV-2025/001",
        invoice_date=date(2025, 10, 15),
        supplier=Party("Acme Supplies", "1 Market Rd, Pune", SUPPLIER_MH),
        buyer=Party("Our Co", "2 Park St, Mumbai", BUYER_MH),
        place_of_supply="27",
        reverse_charge=False,
        lines=(intra_line(),),
        subtotal=D("1000.00"),
        tax_totals=((TaxHead.CGST, D("90.00")), (TaxHead.SGST, D("90.00"))),
        grand_total=D("1180.00"),
        irn=IRN,
        has_signed_qr=True,
    )
    return replace(base, **kw)


def inter_invoice(**kw) -> Invoice:
    """A clean inter-state invoice: Maharashtra supplier to Karnataka buyer, IGST 18%."""
    line = intra_line(taxes=(LineTax(TaxHead.IGST, D("18"), D("180.00")),))
    base = intra_invoice(
        buyer=Party("Our Co", "3 MG Rd, Bengaluru", BUYER_KA),
        place_of_supply="29",
        lines=(line,),
        tax_totals=((TaxHead.IGST, D("180.00")),),
    )
    return replace(base, **kw)


@pytest.fixture
def gst() -> IndiaGST:
    return IndiaGST(OUR_GSTINS)


def rule_ids(flags: list[Flag]) -> set[str]:
    return {f.rule_id for f in flags}


def only(flags: list[Flag], rule_id: str) -> Flag:
    matching = [f for f in flags if f.rule_id == rule_id]
    assert len(matching) == 1, flags
    return matching[0]


# Interface ---------------------------------------------------------------------------------


def test_india_gst_implements_tax_regime(gst):
    assert isinstance(gst, TaxRegime)


@pytest.mark.parametrize("make", [intra_invoice, inter_invoice])
def test_clean_invoices_have_no_flags(gst, make):
    assert gst.validate(make()) == []


def test_every_flag_carries_rule_severity_message_and_fix(gst):
    flags = gst.validate(Invoice())
    assert flags
    for f in flags:
        assert f.rule_id.startswith("GST")
        assert isinstance(f.severity, Severity)
        assert f.message and f.fix


def test_floats_are_rejected_for_money():
    with pytest.raises(TypeError):
        InvoiceLine(taxable_value=1000.0)
    with pytest.raises(TypeError):
        Invoice(grand_total=1180.0)


# §1 GSTIN ----------------------------------------------------------------------------------


def test_supplier_gstin_wrong_check_character_is_block(gst):
    flags = gst.validate(intra_invoice(supplier=Party("Acme", "Pune", "27AAPFU0939F1ZX")))
    flag = only(flags, "GST1.SUPPLIER_GSTIN_INVALID")
    assert flag.severity is Severity.BLOCK
    assert "check character" in flag.message


@pytest.mark.parametrize("bad", ["27AAPFU0939F1Z", "27aapfu0939f1zv!", "27AAPFU0939F0ZV"])
def test_supplier_gstin_bad_format_is_block(gst, bad):
    flags = gst.validate(intra_invoice(supplier=Party("Acme", "Pune", bad)))
    assert only(flags, "GST1.SUPPLIER_GSTIN_INVALID").severity is Severity.BLOCK


def test_supplier_gstin_unknown_state_code_is_block(gst):
    from app.tax.gstin import check_character

    gstin = "50AAPFU0939F1Z"
    gstin += check_character(gstin)
    flag = only(
        gst.validate(intra_invoice(supplier=Party("A", "B", gstin))), "GST1.SUPPLIER_GSTIN_INVALID"
    )
    assert "state code" in flag.message


def test_invalid_supplier_gstin_still_gets_tax_head_check(gst):
    """A wrong check character does not hide a wrong tax head: both are reported."""
    line = intra_line(taxes=(LineTax(TaxHead.IGST, D("18"), D("180.00")),))
    inv = intra_invoice(
        supplier=Party("Acme", "Pune", "27AAPFU0939F1ZX"), lines=(line,), tax_totals=()
    )
    assert {"GST1.SUPPLIER_GSTIN_INVALID", "GST3.WRONG_TAX_HEAD"} <= rule_ids(gst.validate(inv))


def test_lowercase_and_spaced_gstin_is_normalised(gst):
    inv = intra_invoice(supplier=Party("Acme", "Pune", " 27aapfu0939f1zv "))
    assert gst.validate(inv) == []


def test_missing_supplier_gstin_is_warn(gst):
    flags = gst.validate(intra_invoice(supplier=Party("Acme", "Pune", None)))
    assert only(flags, "GST1.SUPPLIER_GSTIN_MISSING").severity is Severity.WARN


def test_buyer_gstin_not_ours_is_block(gst):
    inv = intra_invoice(buyer=Party("Our Co", "Mumbai", SUPPLIER_MH))  # valid, but not ours
    assert only(gst.validate(inv), "GST1.BUYER_GSTIN_NOT_OURS").severity is Severity.BLOCK


def test_buyer_gstin_invalid_is_block(gst):
    inv = intra_invoice(buyer=Party("Our Co", "Mumbai", "27AAGCB7383J1Z9"))
    assert only(gst.validate(inv), "GST1.BUYER_GSTIN_INVALID").severity is Severity.BLOCK


def test_state_code_table_is_data():
    from app.tax.gstin import state_codes

    codes = state_codes()
    assert {f"{n:02d}" for n in range(1, 39)} | {"97", "99"} == set(codes)
    assert codes["04"].ut_without_legislature and not codes["07"].ut_without_legislature


# §2 Mandatory contents ---------------------------------------------------------------------


@pytest.mark.parametrize("field", ["invoice_number", "invoice_date", "grand_total"])
def test_missing_number_date_or_total_is_block(gst, field):
    flags = gst.validate(intra_invoice(**{field: None}))
    flag = next(f for f in flags if f.rule_id == "GST2.MISSING_REQUIRED")
    assert flag.severity is Severity.BLOCK and flag.field == field


@pytest.mark.parametrize(
    ("kw", "field"),
    [
        ({"supplier": Party(None, "Pune", SUPPLIER_MH)}, "supplier.name"),
        ({"supplier": Party("Acme", " ", SUPPLIER_MH)}, "supplier.address"),
        ({"buyer": Party(None, "Mumbai", BUYER_MH)}, "buyer.name"),
        ({"buyer": Party("Our Co", None, BUYER_MH)}, "buyer.address"),
        ({"lines": (intra_line(description=None),)}, "lines[0].description"),
        ({"lines": (intra_line(quantity=None, unit_price=None),)}, "lines[0].quantity"),
    ],
)
def test_other_missing_contents_are_warn(gst, kw, field):
    flag = only(gst.validate(intra_invoice(**kw)), "GST2.MISSING_FIELD")
    assert flag.severity is Severity.WARN and flag.field == field


def test_missing_quantity_on_a_service_line_is_fine(gst):
    line = intra_line(hsn_sac="998314", quantity=None, unit_price=None)
    assert gst.validate(intra_invoice(lines=(line,))) == []


def test_missing_place_of_supply_on_inter_state_is_warn(gst):
    flags = gst.validate(inter_invoice(place_of_supply=None))
    flag = only(flags, "GST2.MISSING_FIELD")
    assert flag.field == "place_of_supply" and flag.severity is Severity.WARN


@pytest.mark.parametrize("number", ["INV-2025/001", "A1", "1234567890123456"])
def test_invoice_number_format_ok(gst, number):
    assert "GST2.INVOICE_NUMBER_FORMAT" not in rule_ids(
        gst.validate(intra_invoice(invoice_number=number))
    )


@pytest.mark.parametrize("number", ["12345678901234567", "INV#001", "INV 001", "INV_1"])
def test_invoice_number_format_violation_is_warn(gst, number):
    flags = gst.validate(intra_invoice(invoice_number=number))
    assert only(flags, "GST2.INVOICE_NUMBER_FORMAT").severity is Severity.WARN


# §3 Tax heads and place of supply ----------------------------------------------------------


def test_igst_on_intra_state_supply_is_block(gst):
    line = intra_line(taxes=(LineTax(TaxHead.IGST, D("18"), D("180.00")),))
    inv = intra_invoice(lines=(line,), tax_totals=((TaxHead.IGST, D("180.00")),))
    flags = [f for f in gst.validate(inv) if f.rule_id == "GST3.WRONG_TAX_HEAD"]
    assert {f.field for f in flags} == {"lines[0].taxes", "tax_totals"}
    assert all(f.severity is Severity.BLOCK for f in flags)
    assert "IGST" in flags[0].message


def test_cgst_sgst_on_inter_state_supply_is_block(gst):
    inv = inter_invoice(
        lines=(intra_line(),),
        tax_totals=((TaxHead.CGST, D("90.00")), (TaxHead.SGST, D("90.00"))),
    )
    flags = [f for f in gst.validate(inv) if f.rule_id == "GST3.WRONG_TAX_HEAD"]
    assert len(flags) == 2 and all(f.severity is Severity.BLOCK for f in flags)


def test_intra_state_with_only_cgst_is_block(gst):
    line = intra_line(taxes=(LineTax(TaxHead.CGST, D("9"), D("90.00")),))
    inv = intra_invoice(lines=(line,), tax_totals=((TaxHead.CGST, D("90.00")),), grand_total=None)
    assert "GST3.WRONG_TAX_HEAD" in rule_ids(gst.validate(inv))


def test_unequal_halves_is_block(gst):
    line = intra_line(
        taxes=(
            LineTax(TaxHead.CGST, D("12"), D("120.00")),
            LineTax(TaxHead.SGST, D("6"), D("60.00")),
        )
    )
    inv = intra_invoice(
        lines=(line,), tax_totals=((TaxHead.CGST, D("120.00")), (TaxHead.SGST, D("60.00")))
    )
    flags = gst.validate(inv)
    assert rule_ids(flags) == {"GST3.UNEQUAL_HALVES"}
    assert only(flags, "GST3.UNEQUAL_HALVES").severity is Severity.BLOCK


def _ut_invoice(second: TaxHead) -> Invoice:
    line = intra_line(
        taxes=(LineTax(TaxHead.CGST, D("9"), D("90.00")), LineTax(second, D("9"), D("90.00")))
    )
    return intra_invoice(
        supplier=Party("UT Supplier", "Sector 17, Chandigarh", SUPPLIER_CH),
        buyer=Party("Our Co", "Sector 22, Chandigarh", BUYER_CH),
        place_of_supply="04",
        lines=(line,),
        tax_totals=((TaxHead.CGST, D("90.00")), (second, D("90.00"))),
    )


def test_ut_without_legislature_takes_cgst_utgst(gst):
    assert gst.validate(_ut_invoice(TaxHead.UTGST)) == []


def test_sgst_in_ut_without_legislature_is_block(gst):
    flags = gst.validate(_ut_invoice(TaxHead.SGST))
    wrong = [f for f in flags if f.rule_id == "GST3.WRONG_TAX_HEAD"]
    assert {f.field for f in wrong} == {"lines[0].taxes", "tax_totals"}
    assert all("UTGST" in f.message and f.severity is Severity.BLOCK for f in wrong)


def test_missing_place_of_supply_is_derived_from_buyer_gstin_as_info(gst):
    flags = gst.validate(intra_invoice(place_of_supply=None))
    flag = only(flags, "GST3.PLACE_OF_SUPPLY_DERIVED")
    assert flag.severity is Severity.INFO and "27" in flag.message
    assert rule_ids(flags) == {"GST3.PLACE_OF_SUPPLY_DERIVED"}  # intra-state: no warn


def test_derived_place_of_supply_still_catches_wrong_head(gst):
    line = intra_line(taxes=(LineTax(TaxHead.IGST, D("18"), D("180.00")),))
    inv = intra_invoice(place_of_supply=None, lines=(line,), tax_totals=())
    assert "GST3.WRONG_TAX_HEAD" in rule_ids(gst.validate(inv))


def test_invalid_place_of_supply_is_warn(gst):
    flags = gst.validate(intra_invoice(place_of_supply="55"))
    assert only(flags, "GST3.PLACE_OF_SUPPLY_INVALID").severity is Severity.WARN


def test_zero_value_heads_are_ignored(gst):
    """A printed `IGST 0.00` column on an intra-state invoice is not a wrong head."""
    inv = intra_invoice(
        tax_totals=(
            (TaxHead.CGST, D("90.00")),
            (TaxHead.SGST, D("90.00")),
            (TaxHead.IGST, D("0.00")),
        )
    )
    assert gst.validate(inv) == []


# §4 Rates ----------------------------------------------------------------------------------


def _at_rate(total_rate: str, on: date, **kw) -> Invoice:
    half = D(total_rate) / 2
    tax = D("1000.00") * half / 100
    line = intra_line(taxes=(LineTax(TaxHead.CGST, half, tax), LineTax(TaxHead.SGST, half, tax)))
    return intra_invoice(
        invoice_date=on,
        lines=(line,),
        tax_totals=((TaxHead.CGST, tax), (TaxHead.SGST, tax)),
        grand_total=D("1000.00") + 2 * tax,
        **kw,
    )


@pytest.mark.parametrize(
    ("rate", "on"),
    [
        ("12", date(2025, 9, 21)),  # old slab, last day before rationalisation
        ("28", date(2024, 4, 1)),
        ("5", date(2025, 9, 22)),
        ("40", date(2025, 9, 22)),  # new slab from its first day
        ("18", date(2023, 1, 1)),
    ],
)
def test_rate_in_table_for_invoice_date_passes(gst, rate, on):
    assert gst.validate(_at_rate(rate, on)) == []


@pytest.mark.parametrize(
    ("rate", "on"),
    [
        ("12", date(2025, 9, 22)),  # 12% slab removed from 22-Sep-2025
        ("28", date(2025, 11, 1)),
        ("40", date(2025, 9, 21)),  # 40% did not exist yet
        ("17", date(2025, 10, 1)),
    ],
)
def test_rate_not_in_table_for_invoice_date_is_warn(gst, rate, on):
    flag = only(gst.validate(_at_rate(rate, on)), "GST4.UNKNOWN_RATE")
    assert flag.severity is Severity.WARN  # C5: never block on the rate table


def test_rate_table_is_data_with_effective_dates_and_last_verified(gst):
    table = gst.rate_table
    assert [s.effective_from for s in table.slab_sets] == [date(2017, 7, 1), date(2025, 9, 22)]
    assert D("12") in table.rates_on(date(2025, 9, 21))
    assert D("12") not in table.rates_on(date(2025, 9, 22))
    # C5: the seed is unverified, so it must not claim a verification date until someone checks it.
    assert table.last_verified is None
    assert "UNVERIFIED" in table.verified_by


def test_custom_rate_table_is_used():
    from app.tax import RateTable

    table = RateTable.from_dict(
        {
            "last_verified": "2026-01-01",
            "slabs": [{"effective_from": "2017-07-01", "rates": ["18", "17"]}],
        }
    )
    assert table.last_verified == date(2026, 1, 1)
    assert IndiaGST(OUR_GSTINS, table).validate(_at_rate("17", date(2025, 10, 1))) == []


def test_rate_table_rejects_unsorted_dates():
    from app.tax.rates import RateSlabSet, RateTable

    with pytest.raises(ValueError):
        RateTable(
            (
                RateSlabSet(date(2025, 9, 22), frozenset()),
                RateSlabSet(date(2017, 7, 1), frozenset()),
            ),
            date(2026, 1, 1),
            "",
        )


# §5 Arithmetic -----------------------------------------------------------------------------


def test_grand_total_off_by_exactly_one_rupee_passes(gst):
    assert gst.validate(intra_invoice(grand_total=D("1181.00"))) == []


def test_grand_total_off_by_one_rupee_one_paisa_is_block(gst):
    flags = gst.validate(intra_invoice(grand_total=D("1181.01")))
    flag = only(flags, "GST5.GRAND_TOTAL")
    assert flag.severity is Severity.BLOCK and "₹1.01" in flag.message


def test_round_off_is_applied(gst):
    line = intra_line(
        unit_price=D("99.95"),
        taxable_value=D("999.50"),
        taxes=(
            LineTax(TaxHead.CGST, D("9"), D("89.96")),
            LineTax(TaxHead.SGST, D("9"), D("89.96")),
        ),
    )
    inv = intra_invoice(
        lines=(line,),
        subtotal=D("999.50"),
        tax_totals=((TaxHead.CGST, D("89.96")), (TaxHead.SGST, D("89.96"))),
        round_off=D("0.58"),
        grand_total=D("1180.00"),
    )
    assert gst.validate(inv) == []


def test_cess_is_added_to_grand_total(gst):
    assert gst.validate(intra_invoice(cess_total=D("50.00"), grand_total=D("1230.00"))) == []
    assert "GST5.GRAND_TOTAL" in rule_ids(gst.validate(intra_invoice(cess_total=D("50.00"))))


def test_line_taxable_value_mismatch_is_block(gst):
    inv = intra_invoice(lines=(intra_line(unit_price=D("100.11")),))  # 1001.10 vs 1000.00
    assert only(gst.validate(inv), "GST5.LINE_TAXABLE_VALUE").severity is Severity.BLOCK


def test_line_discount_is_subtracted(gst):
    line = intra_line(unit_price=D("110.00"), discount=D("100.00"))
    assert gst.validate(intra_invoice(lines=(line,))) == []


def test_line_tax_amount_mismatch_is_block(gst):
    line = intra_line(
        taxes=(
            LineTax(TaxHead.CGST, D("9"), D("91.01")),
            LineTax(TaxHead.SGST, D("9"), D("90.00")),
        )
    )
    flags = gst.validate(intra_invoice(lines=(line,)))
    assert only(flags, "GST5.LINE_TAX_AMOUNT").severity is Severity.BLOCK


def test_subtotal_mismatch_is_block(gst):
    flags = gst.validate(intra_invoice(subtotal=D("1001.01"), grand_total=D("1181.01")))
    assert only(flags, "GST5.SUBTOTAL").severity is Severity.BLOCK


def test_tax_summary_mismatch_is_block(gst):
    inv = intra_invoice(
        tax_totals=((TaxHead.CGST, D("92.00")), (TaxHead.SGST, D("90.00"))),
        grand_total=D("1182.00"),
    )
    flag = only(gst.validate(inv), "GST5.TAX_TOTAL")
    assert flag.severity is Severity.BLOCK and "CGST" in flag.message


def test_totals_use_line_sums_when_no_summary(gst):
    assert gst.validate(intra_invoice(tax_totals=(), subtotal=None)) == []
    flags = gst.validate(intra_invoice(tax_totals=(), subtotal=None, grand_total=D("1190.00")))
    assert rule_ids(flags) == {"GST5.GRAND_TOTAL"}


# §6 HSN / SAC ------------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["8471", "847130", "84713010", "9983", "8471.30"])
def test_hsn_with_four_or_more_digits_passes(gst, code):
    line = intra_line(hsn_sac=code)
    assert "GST6" not in {r[:4] for r in rule_ids(gst.validate(intra_invoice(lines=(line,))))}


def test_missing_hsn_is_warn(gst):
    flags = gst.validate(intra_invoice(lines=(intra_line(hsn_sac=None),)))
    assert only(flags, "GST6.HSN_MISSING").severity is Severity.WARN


@pytest.mark.parametrize("code", ["847", "84", "ABCD"])
def test_hsn_under_four_digits_is_warn(gst, code):
    flags = gst.validate(intra_invoice(lines=(intra_line(hsn_sac=code),)))
    assert only(flags, "GST6.HSN_TOO_SHORT").severity is Severity.WARN


# §7 E-invoicing ----------------------------------------------------------------------------


def test_irn_with_signed_qr_passes(gst):
    assert "GST7.NOT_E_INVOICE" not in rule_ids(gst.validate(intra_invoice()))


@pytest.mark.parametrize(
    "kw", [{"irn": None, "has_signed_qr": False}, {"has_signed_qr": False}, {"irn": "abc"}]
)
def test_no_e_invoice_is_info(gst, kw):
    flag = only(gst.validate(intra_invoice(**kw)), "GST7.NOT_E_INVOICE")
    assert flag.severity is Severity.INFO


def test_no_e_invoice_is_warn_when_vendor_is_mandated(gst):
    inv = intra_invoice(irn=None, has_signed_qr=False)
    flag = only(gst.validate(inv, vendor_e_invoice_mandated=True), "GST7.NOT_E_INVOICE")
    assert flag.severity is Severity.WARN
    assert gst.validate(intra_invoice(), vendor_e_invoice_mandated=True) == []


# Purity ------------------------------------------------------------------------------------


def test_validate_is_deterministic_and_does_not_mutate(gst):
    inv = intra_invoice(grand_total=D("2000.00"), irn=None)
    assert gst.validate(inv) == gst.validate(inv)
    assert inv == intra_invoice(grand_total=D("2000.00"), irn=None)
