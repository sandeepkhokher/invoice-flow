"""Deterministic checks on an extraction and the confidence merge (spec FR-21, §5.3).

Confidence = the model's rating, lowered one level when a deterministic check fails:
- the value is absent from the PDF text layer (when the PDF has one),
- a GSTIN fails its checksum or format,
- the arithmetic does not reconcile (the tax layer's §5 checks, ₹1 tolerance),
- the date is in the future or more than 18 months old.
A value that does not parse as a number or date at all is set to `low`.

Also maps the extraction onto the tax layer's `Invoice` (S2) so `IndiaGST` can validate it.
Pure functions: no DB, no network. Money is `Decimal` throughout.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader

from app.extraction.schema import (
    CONFIDENCE_LEVELS,
    Confidence,
    ExtractedInvoice,
    FieldValue,
    flatten,
)
from app.tax.flags import Flag as TaxFlag
from app.tax.gstin import gstin_problem
from app.tax.invoice import Invoice as TaxInvoice
from app.tax.invoice import InvoiceLine, LineTax, Party, TaxHead

MAX_AGE_MONTHS = 18
MIN_TEXT_LAYER_CHARS = 40  # fewer extractable characters: treat the PDF as a scan
HEADS = ("cgst", "sgst", "utgst", "igst")
HEAD_OF = {"cgst": TaxHead.CGST, "sgst": TaxHead.SGST, "utgst": TaxHead.UTGST, "igst": TaxHead.IGST}

# Fields checked against the text layer: what a misread would most hurt. Line-level numbers
# are left to the arithmetic check, since quantities and rates print in too many forms.
TEXT_CHECKED_PREFIXES = (
    "invoice_number",
    "invoice_date",
    "supplier.name",
    "supplier.gstin",
    "buyer.gstin",
    "subtotal",
    "tax_totals.",
    "grand_total",
    "irn",
)
DATE_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%d %b %Y", "%d-%b-%Y", "%d %B %Y", "%Y-%m-%d")

_SQUASH = re.compile(r"[\s,₹]+")


# --- parsing --------------------------------------------------------------------------------


def parse_amount(value: str | None) -> Decimal | None:
    """A decimal from a model string. Tolerates commas, spaces and ₹; None if it is not one."""
    if value is None:
        return None
    cleaned = _SQUASH.sub("", value).removeprefix("Rs.").removeprefix("INR")
    if not cleaned:
        return None
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    return amount if amount.is_finite() else None


def parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def normalise_gstin(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", "", value).upper()
    return cleaned or None


# --- text layer -----------------------------------------------------------------------------


def pdf_text_layer(data: bytes) -> str | None:
    """The PDF's extractable text, or None for a scan (no usable text layer) or a bad file."""
    try:
        reader = PdfReader(io.BytesIO(data))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:  # damaged PDFs were rejected at intake; be safe anyway
        return None
    squashed = squash(text)
    return squashed if len(squashed) >= MIN_TEXT_LAYER_CHARS else None


def squash(text: str) -> str:
    """Uppercase with whitespace, commas and ₹ removed, so layout does not matter."""
    return _SQUASH.sub("", text).upper()


def _amount_forms(amount: Decimal) -> set[str]:
    forms = {f"{amount:.2f}"}
    if amount == amount.to_integral_value():
        forms.add(f"{amount:.0f}")
    return forms


def _date_forms(day: date) -> set[str]:
    return {squash(day.strftime(fmt)) for fmt in DATE_FORMATS}


def in_text_layer(name: str, field: FieldValue, text: str) -> bool:
    """True when the field's value (in any usual printed form) appears in the squashed text."""
    value = field.value
    if name == "invoice_date":
        day = parse_date(value)  # type: ignore[arg-type]
        return day is None or any(form in text for form in _date_forms(day))
    if name in ("subtotal", "grand_total") or name.startswith("tax_totals."):
        amount = parse_amount(value)  # type: ignore[arg-type]
        if amount is None:
            return True  # unparsable: handled by the parse check
        return any(squash(form) in text for form in _amount_forms(abs(amount)))
    return squash(str(value)) in text


# --- mapping to the tax layer ----------------------------------------------------------------


def to_tax_invoice(inv: ExtractedInvoice) -> TaxInvoice:
    """The extraction as S2's `Invoice`. Unparsable numbers and dates become None."""
    lines = []
    for ln in inv.line_items:
        rate = parse_amount(ln.rate.value)
        taxes = []
        for head in HEADS:
            amount = parse_amount(getattr(ln, head).value)
            if amount is None or amount == 0:
                continue
            tax_head = HEAD_OF[head]
            head_rate = None
            if rate is not None:
                head_rate = rate if tax_head is TaxHead.IGST else rate / 2
            taxes.append(LineTax(head=tax_head, rate=head_rate, amount=amount))
        lines.append(
            InvoiceLine(
                description=ln.description.value,
                hsn_sac=ln.hsn_sac.value,
                quantity=parse_amount(ln.quantity.value),
                unit_price=parse_amount(ln.unit_price.value),
                discount=parse_amount(ln.discount.value),
                taxable_value=parse_amount(ln.taxable_value.value),
                taxes=tuple(taxes),
                cess=parse_amount(ln.cess.value),
            )
        )
    ts = inv.tax_summary
    tax_totals = []
    for head in HEADS:
        amount = parse_amount(getattr(ts, head).value)
        if amount is not None and amount != 0:
            tax_totals.append((HEAD_OF[head], amount))
    return TaxInvoice(
        invoice_number=inv.invoice_number.value,
        invoice_date=parse_date(inv.invoice_date.value),
        supplier=Party(
            name=inv.supplier.name.value,
            address=inv.supplier.address.value,
            gstin=normalise_gstin(inv.supplier.gstin.value),
        ),
        buyer=Party(
            name=inv.buyer.name.value,
            address=inv.buyer.address.value,
            gstin=normalise_gstin(inv.buyer.gstin.value),
        ),
        place_of_supply=inv.place_of_supply.value,
        reverse_charge=inv.reverse_charge.value,
        lines=tuple(lines),
        subtotal=parse_amount(ts.subtotal.value),
        tax_totals=tuple(tax_totals),
        cess_total=parse_amount(ts.cess.value),
        round_off=parse_amount(ts.round_off.value),
        grand_total=parse_amount(ts.grand_total.value),
        irn=inv.irn.value,
        has_signed_qr=bool(inv.has_signed_qr.value),
    )


# --- confidence -----------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoredField:
    name: str
    value: str | None
    model_confidence: Confidence
    confidence: Confidence
    page: int | None
    failed_checks: tuple[str, ...] = ()


def lower(level: Confidence) -> Confidence:
    return CONFIDENCE_LEVELS[max(0, CONFIDENCE_LEVELS.index(level) - 1)]


def _months_before(day: date, months: int) -> date:
    year, month = divmod(day.year * 12 + day.month - 1 - months, 12)
    month += 1
    for d in (day.day, 30, 29, 28):
        try:
            return date(year, month, d)
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


def _arithmetic_fields(flag: TaxFlag, names: list[str]) -> set[str]:
    """The stored field names an arithmetic (GST5) flag's `field` points at."""
    target = flag.field or ""
    if target == "tax_totals":
        return {f"tax_totals.{h}" for h in HEADS}
    m = re.fullmatch(r"lines\[(\d+)\]\.taxes", target)
    if m:
        i = m.group(1)
        return {f"lines[{i}].{h}" for h in (*HEADS, "rate")}
    return {target} & set(names)


_LINE_AMOUNT = re.compile(
    r"lines\[\d+\]\.(quantity|unit_price|discount|taxable_value|rate|cgst|sgst|utgst|igst|cess)"
)


def _is_amount(name: str) -> bool:
    return (
        name in ("subtotal", "grand_total", "cess_total", "round_off")
        or name.startswith("tax_totals.")
        or _LINE_AMOUNT.fullmatch(name) is not None
    )


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def score(
    inv: ExtractedInvoice,
    *,
    tax_flags: list[TaxFlag],
    text_layer: str | None,
    today: date,
) -> list[ScoredField]:
    """Every named field with its merged confidence and the checks it failed."""
    fields = flatten(inv)
    names = list(fields)
    failed: dict[str, list[str]] = {n: [] for n in names}
    force_low: set[str] = set()

    for name, field in fields.items():
        if field.value is None:
            continue
        if _is_amount(name) and parse_amount(field.value) is None:  # type: ignore[arg-type]
            force_low.add(name)
            failed[name].append("not_a_number")
        checked = text_layer is not None and name.startswith(TEXT_CHECKED_PREFIXES)
        if checked and not in_text_layer(name, field, text_layer):  # type: ignore[arg-type]
            failed[name].append("not_in_text_layer")

    for name in ("supplier.gstin", "buyer.gstin"):
        gstin = normalise_gstin(fields[name].value)  # type: ignore[arg-type]
        if gstin is not None and gstin_problem(gstin):
            failed[name].append("gstin_checksum")

    for flag in tax_flags:
        if flag.rule_id.startswith("GST5."):
            for name in _arithmetic_fields(flag, names):
                if "arithmetic" not in failed.get(name, []):
                    failed.setdefault(name, []).append("arithmetic")

    raw_date = fields["invoice_date"].value
    if raw_date is not None:
        day = parse_date(raw_date)  # type: ignore[arg-type]
        if day is None:
            force_low.add("invoice_date")
            failed["invoice_date"].append("not_a_date")
        elif day > today:
            failed["invoice_date"].append("date_in_future")
        elif day < _months_before(today, MAX_AGE_MONTHS):
            failed["invoice_date"].append("date_too_old")

    scored = []
    for name, field in fields.items():
        checks = tuple(failed.get(name, ()))
        confidence: Confidence = field.confidence
        if name in force_low:
            confidence = "low"
        elif checks:
            confidence = lower(confidence)  # one level, however many checks failed
        scored.append(
            ScoredField(
                name=name,
                value=_as_text(field.value),
                model_confidence=field.confidence,
                confidence=confidence,
                page=field.page,
                failed_checks=checks,
            )
        )
    return scored
