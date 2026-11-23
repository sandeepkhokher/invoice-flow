"""Five invoice layout templates, drawn onto a backend-neutral canvas.

A layout turns an ``Invoice`` into pages of drawing operations (text, lines, boxes, a QR
code) in millimetres on A4. ``render.py`` turns those pages into a digital PDF (with a text
layer) or into raster images. Long invoices flow onto more pages.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from evals.synth.catalog import STATES
from evals.synth.model import CGST_UTGST, IGST, Invoice, Line

PAGE_W, PAGE_H = 210.0, 297.0
LEFT, RIGHT = 12.0, 198.0
CONTENT_BOTTOM = 272.0
FOOTER_Y = 288.0
SYNTHETIC_NOTICE = "Synthetic sample for software testing - not a real invoice"
PT_MM = 0.3528


@dataclass
class Canvas:
    pages: list[list[tuple]] = field(default_factory=lambda: [[]])

    @property
    def ops(self) -> list[tuple]:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append([])

    def text(self, x, y, s, size=9.0, bold=False, align="L", color=0) -> None:
        """Text with its baseline at ``y``; ``align`` is L, R or C about ``x``."""
        if s:
            s = str(s)
            s.encode("latin-1")  # the PDF core fonts are latin-1; fail loudly otherwise
            self.ops.append(("text", x, y, s, size, bold, align, color))

    def line(self, x1, y1, x2, y2, width=0.2) -> None:
        self.ops.append(("line", x1, y1, x2, y2, width))

    def rect(self, x, y, w, h, fill=None, stroke=True) -> None:
        self.ops.append(("rect", x, y, w, h, fill, stroke))

    def qr(self, x, y, size, data) -> None:
        self.ops.append(("qr", x, y, size, data))


def fits(s: str, width_mm: float, size: float) -> bool:
    """Conservative width check (Helvetica averages about 0.5 em; allow 0.56)."""
    return len(s) * size * PT_MM * 0.56 <= width_mm


# ---------- formatting helpers ----------


def group_indian(d: Decimal) -> str:
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join([*parts, tail])
    return f"{sign}{whole}.{frac}"


def group_intl(d: Decimal) -> str:
    return f"{d:,.2f}"


def plain(d: Decimal) -> str:
    return f"{d:.2f}"


def qty(q: Decimal) -> str:
    return str(int(q)) if q == q.to_integral_value() else f"{q:.2f}"


def rate_str(r: Decimal) -> str:
    return f"{r.normalize():f}%"


_ONES = [
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
]
_TENS = ["_", "_", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _words_below_1000(n: int) -> str:
    out = []
    if n >= 100:
        out.append(f"{_ONES[n // 100]} Hundred")
        n %= 100
    if n >= 20:
        out.append(_TENS[n // 10] + (f" {_ONES[n % 10]}" if n % 10 else ""))
    elif n:
        out.append(_ONES[n])
    return " ".join(out)


def amount_in_words(d: Decimal) -> str:
    """Indian-style words: 'Rupees One Lakh Two Thousand Three Hundred and Fifty Paise Only'."""
    rupees = int(d)
    paise = int((d - rupees) * 100)
    parts = []
    for unit, size in (("Crore", 10_000_000), ("Lakh", 100_000), ("Thousand", 1000)):
        if rupees >= size:
            parts.append(f"{_words_below_1000(rupees // size)} {unit}")
            rupees %= size
    if rupees or not parts:
        parts.append(_words_below_1000(rupees))
    text = "Rupees " + " ".join(p for p in parts if p)
    if paise:
        text += f" and {_words_below_1000(paise)} Paise"
    return text + " Only"


def state_label(code: str) -> str:
    return f"{STATES[code][0]} ({code})"


# ---------- table columns ----------


@dataclass
class Col:
    title: str
    width: float
    align: str = "L"


def tax_cols(inv: Invoice, money_w: float) -> list[Col]:
    if inv.printed_mode == IGST:
        return [Col("IGST", money_w, "R")]
    second = "UTGST" if inv.printed_mode == CGST_UTGST else "SGST"
    return [Col("CGST", money_w, "R"), Col(second, money_w, "R")]


def tax_vals(inv: Invoice, ln: Line, fmt) -> list[str]:
    if inv.printed_mode == IGST:
        return [fmt(ln.igst)]
    second = ln.utgst if inv.printed_mode == CGST_UTGST else ln.sgst
    return [fmt(ln.cgst), fmt(second)]


def summary_rows(inv: Invoice, fmt) -> list[tuple[str, str]]:
    """(label, amount) rows for the totals block, as printed."""
    rows = [("Taxable Value", fmt(inv.taxable_total))]
    if inv.printed_mode == IGST:
        rows.append(("IGST", fmt(inv.igst_total)))
    else:
        rows.append(("CGST", fmt(inv.cgst_total)))
        if inv.printed_mode == CGST_UTGST:
            rows.append(("UTGST", fmt(inv.utgst_total)))
        else:
            rows.append(("SGST", fmt(inv.sgst_total)))
    if inv.round_off:
        rows.append(("Round Off", fmt(inv.round_off)))
    return rows


def rate_breakup(inv: Invoice) -> list[tuple[Decimal, Decimal, Decimal]]:
    """(rate, taxable, tax) per GST rate, for HSN/rate summary tables."""
    acc: dict[Decimal, list[Decimal]] = {}
    for ln in inv.lines:
        a = acc.setdefault(ln.rate, [Decimal(0), Decimal(0)])
        a[0] += ln.taxable_value
        a[1] += ln.tax
    return [(r, v[0], v[1]) for r, v in sorted(acc.items())]


# ---------- base layout ----------


class Layout:
    name = "base"
    size = 8.0  # table font size
    row_h = 5.5
    grid = False
    header_fill: int | None = 230
    date_fmt = "%d-%m-%Y"
    money = staticmethod(plain)

    def render(self, inv: Invoice) -> Canvas:
        cv = Canvas()
        y = self.header(cv, inv)
        y = self.table(cv, inv, y)
        if y + self.totals_height(inv) > CONTENT_BOTTOM:
            cv.new_page()
            y = self.cont_header(cv, inv)
        self.totals(cv, inv, y)
        self.footers(cv, inv)
        return cv

    # subclasses override these
    def header(self, cv: Canvas, inv: Invoice) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def columns(self, inv: Invoice) -> list[Col]:  # pragma: no cover - abstract
        raise NotImplementedError

    def row(self, inv: Invoice, i: int, ln: Line) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def totals_height(self, inv: Invoice) -> float:  # pragma: no cover - abstract
        raise NotImplementedError

    def totals(self, cv: Canvas, inv: Invoice, y: float) -> float:  # pragma: no cover
        raise NotImplementedError

    def cont_header(self, cv: Canvas, inv: Invoice) -> float:
        cv.text(LEFT, 16, inv.supplier.name, 10, bold=True)
        cv.text(RIGHT, 16, f"Invoice No: {inv.number} (continued)", 9, align="R")
        cv.line(LEFT, 19, RIGHT, 19, 0.3)
        return 24

    def footers(self, cv: Canvas, inv: Invoice) -> None:
        n = len(cv.pages)
        for i, page in enumerate(cv.pages, 1):
            page.append(("text", LEFT, FOOTER_Y, SYNTHETIC_NOTICE, 6.5, False, "L", 90))
            page.append(("text", RIGHT, FOOTER_Y, f"Page {i} of {n}", 7, False, "R", 0))

    def table_header(self, cv: Canvas, cols: list[Col], y: float) -> float:
        h = self.row_h + 1
        if self.header_fill is not None:
            cv.rect(LEFT, y, RIGHT - LEFT, h, fill=self.header_fill, stroke=self.grid)
        x = LEFT
        for c in cols:
            self._cell(cv, c, x, y + h - 1.8, c.title, bold=True)
            x += c.width
        if self.grid:
            self._vlines(cv, cols, y, y + h)
        else:
            cv.line(LEFT, y + h, RIGHT, y + h, 0.3)
        return y + h

    def table(self, cv: Canvas, inv: Invoice, y: float) -> float:
        cols = self.columns(inv)
        total_w = sum(c.width for c in cols)
        assert abs(total_w - (RIGHT - LEFT)) < 0.01, (self.name, total_w)
        y = self.table_header(cv, cols, y)
        for i, ln in enumerate(inv.lines, 1):
            if y + self.row_h > CONTENT_BOTTOM:
                cv.new_page()
                y = self.table_header(cv, cols, self.cont_header(cv, inv))
            x = LEFT
            for c, val in zip(cols, self.row(inv, i, ln), strict=True):
                if not fits(val, c.width - 2, self.size):
                    raise ValueError(f"{self.name}: {val!r} does not fit column {c.title!r}")
                self._cell(cv, c, x, y + self.row_h - 1.6, val)
                x += c.width
            if self.grid:
                self._vlines(cv, cols, y, y + self.row_h)
                cv.line(LEFT, y + self.row_h, RIGHT, y + self.row_h, 0.15)
            y += self.row_h
        cv.line(LEFT, y, RIGHT, y, 0.3)
        return y + 3

    def _cell(self, cv: Canvas, c: Col, x: float, baseline: float, s: str, bold=False) -> None:
        if c.align == "R":
            cv.text(x + c.width - 1, baseline, s, self.size, bold, "R")
        elif c.align == "C":
            cv.text(x + c.width / 2, baseline, s, self.size, bold, "C")
        else:
            cv.text(x + 1, baseline, s, self.size, bold, "L")

    def _vlines(self, cv: Canvas, cols: list[Col], y1: float, y2: float) -> None:
        x = LEFT
        cv.line(x, y1, x, y2, 0.15)
        for c in cols:
            x += c.width
            cv.line(x, y1, x, y2, 0.15)

    # shared blocks
    def party_block(self, cv, x, y, title, p, size=8.5, width=88.0) -> float:
        if title:
            cv.text(x, y, title, size - 0.5, bold=True, color=80)
            y += 4.2
        cv.text(x, y, p.name, size + 1, bold=True)
        y += 4.6
        for a in p.address:
            cv.text(x, y, a, size)
            y += 4
        cv.text(x, y, f"GSTIN: {p.gstin}", size, bold=True)
        y += 4
        cv.text(x, y, f"State: {state_label(p.state_code)}", size)
        return y + 4

    def bank_block(self, cv, x, y, inv, size=8.0) -> float:
        b = inv.bank
        cv.text(x, y, "Bank details", size, bold=True)
        rows = [
            f"Bank: {b.bank_name}",
            f"A/c name: {b.account_name}",
            f"A/c no: {b.account_number}",
            f"IFSC: {b.ifsc}   Branch: {b.branch}",
        ]
        for r in rows:
            y += 3.8
            cv.text(x, y, r, size)
        return y + 4

    def irn_block(self, cv, x, y, inv, size=7.0) -> float:
        if not inv.e_invoice:
            return y
        cv.text(x, y, f"IRN: {inv.irn}", size)
        cv.text(x, y + 3.6, f"Ack No: {inv.ack_no}   Ack Date: {inv.date:%d-%m-%Y}", size)
        return y + 7.5

    def summary_box(self, cv, x, y, inv, w=70.0, size=9.0) -> float:
        for label, val in summary_rows(inv, self.money):
            cv.text(x, y, label, size)
            cv.text(x + w, y, val, size, align="R")
            y += 5
        cv.line(x, y - 3.5, x + w, y - 3.5, 0.3)
        cv.text(x, y + 1, "Grand Total (INR)", size + 1, bold=True)
        cv.text(x + w, y + 1, self.money(inv.grand_total), size + 1, bold=True, align="R")
        return y + 7


# ---------- 1. classic ----------


class Classic(Layout):
    name = "classic"
    size = 7.5
    money = staticmethod(group_indian)

    def header(self, cv, inv):
        cv.text(PAGE_W / 2, 16, "TAX INVOICE", 15, bold=True, align="C")
        cv.text(PAGE_W / 2, 21, "(Original for Recipient)", 7.5, align="C")
        y = self.party_block(cv, LEFT, 30, "", inv.supplier, 8.5)
        mx = 125.0
        meta = [
            ("Invoice No", inv.number),
            ("Invoice Date", inv.date.strftime(self.date_fmt)),
            ("Place of Supply", state_label(inv.place_of_supply)),
            ("Reverse Charge", "No"),
        ]
        my = 30.0
        if inv.e_invoice:
            cv.qr(RIGHT - 28, 26, 28, inv.qr_payload)
            mx = 100.0
        for k, v in meta:
            cv.text(mx, my, f"{k}:", 8.5, bold=True)
            cv.text(mx + 27, my, v, 8.5)
            my += 5
        y = max(y, my, 58 if inv.e_invoice else 0) + 2
        y = self.irn_block(cv, LEFT, y, inv) + 1
        cv.line(LEFT, y, RIGHT, y, 0.4)
        y = self.party_block(cv, LEFT, y + 5, "Bill To", inv.buyer, 8.5)
        return y + 2

    def columns(self, inv):
        tc = tax_cols(inv, 18)
        desc = 186 - 6 - 14 - 10 - 11 - 18 - 15 - 20 - 10 - 18 * len(tc)
        return [
            Col("#", 6, "C"),
            Col("Description", desc),
            Col("HSN/SAC", 14),
            Col("Qty", 10, "R"),
            Col("Unit", 11),
            Col("Rate", 18, "R"),
            Col("Disc", 15, "R"),
            Col("Taxable", 20, "R"),
            Col("GST %", 10, "R"),
            *tc,
        ]

    def row(self, inv, i, ln):
        m = self.money
        return [
            str(i),
            ln.description,
            ln.hsn,
            qty(ln.quantity),
            ln.unit,
            m(ln.unit_price),
            m(ln.discount),
            m(ln.taxable_value),
            rate_str(ln.rate),
            *tax_vals(inv, ln, m),
        ]

    def totals_height(self, inv):
        return 58

    def totals(self, cv, inv, y):
        self.summary_box(cv, RIGHT - 75, y + 3, inv, w=75)
        self.bank_block(cv, LEFT, y + 3, inv)
        cv.text(LEFT, y + 34, amount_in_words(inv.grand_total), 8, bold=True)
        cv.text(RIGHT, y + 48, f"For {inv.supplier.name}", 8.5, bold=True, align="R")
        cv.text(RIGHT, y + 56, "Authorised Signatory", 8, align="R")
        return y + 58


# ---------- 2. modern ----------


class Modern(Layout):
    name = "modern"
    size = 8.5
    row_h = 6.5
    header_fill = 215
    date_fmt = "%d %b %Y"
    money = staticmethod(group_intl)

    def header(self, cv, inv):
        cv.rect(0, 0, PAGE_W, 30, fill=45, stroke=False)
        cv.text(LEFT, 14, inv.supplier.name, 16, bold=True, color=255)
        cv.text(LEFT, 21, ", ".join(inv.supplier.address), 8, color=255)
        cv.text(LEFT, 26, f"GSTIN {inv.supplier.gstin}", 8.5, bold=True, color=255)
        cv.text(RIGHT, 14, "Invoice", 18, bold=True, align="R", color=255)
        cv.text(RIGHT, 21, inv.supplier.email, 8, align="R", color=255)
        cv.text(RIGHT, 26, f"State {state_label(inv.supplier.state_code)}", 8, align="R", color=255)
        y = self.party_block(cv, LEFT, 40, "BILLED TO", inv.buyer, 8.5)
        mx, my = 120.0, 40.0
        for k, v in [
            ("INVOICE #", inv.number),
            ("DATE", inv.date.strftime(self.date_fmt)),
            ("PLACE OF SUPPLY", state_label(inv.place_of_supply)),
        ]:
            cv.text(mx, my, k, 7.5, bold=True, color=80)
            cv.text(RIGHT, my, v, 9, align="R")
            my += 6
        if inv.e_invoice:
            cv.qr(RIGHT - 24, my - 1, 24, inv.qr_payload)
            my += 25
        y = max(y, my) + 2
        y = self.irn_block(cv, LEFT, y, inv)
        return y + 2

    def columns(self, inv):
        return [
            Col("Item", 66),
            Col("HSN", 18),
            Col("Qty", 18, "R"),
            Col("Unit price", 26, "R"),
            Col("Discount", 20, "R"),
            Col("GST", 14, "R"),
            Col("Amount", 24, "R"),
        ]

    def row(self, inv, i, ln):
        m = self.money
        return [
            ln.description,
            ln.hsn,
            f"{qty(ln.quantity)} {ln.unit}",
            m(ln.unit_price),
            m(ln.discount) if ln.discount else "-",
            rate_str(ln.rate),
            m(ln.taxable_value),
        ]

    def totals_height(self, inv):
        return 50 + 5 * len(rate_breakup(inv))

    def totals(self, cv, inv, y):
        y += 3
        cv.text(LEFT, y, "Tax summary", 8.5, bold=True)
        y += 5
        heads = (
            ["IGST"]
            if inv.printed_mode == IGST
            else ["CGST", "UTGST" if inv.printed_mode == CGST_UTGST else "SGST"]
        )
        cv.text(LEFT, y, "Rate", 7.5, bold=True)
        cv.text(LEFT + 40, y, "Taxable", 7.5, bold=True, align="R")
        for j, h in enumerate(heads):
            cv.text(LEFT + 65 + 25 * j, y, h, 7.5, bold=True, align="R")
        for rate, taxable, tax in rate_breakup(inv):
            y += 4.5
            cv.text(LEFT, y, rate_str(rate), 7.5)
            cv.text(LEFT + 40, y, self.money(taxable), 7.5, align="R")
            per = tax if len(heads) == 1 else tax / 2
            for j in range(len(heads)):
                cv.text(LEFT + 65 + 25 * j, y, self.money(per), 7.5, align="R")
        self.summary_box(cv, RIGHT - 70, y - 4.5 * len(rate_breakup(inv)) - 5, inv)
        y += 10
        self.bank_block(cv, LEFT, y, inv)
        cv.text(RIGHT, y + 18, "Thank you for your business", 8, align="R", color=80)
        return y + 25


# ---------- 3. compact ----------


class Compact(Layout):
    name = "compact"
    size = 7.0
    row_h = 4.6
    header_fill = None
    date_fmt = "%d/%m/%Y"
    money = staticmethod(plain)

    def header(self, cv, inv):
        s = inv.supplier
        cv.text(LEFT, 14, s.name.upper(), 12, bold=True)
        cv.text(LEFT, 19, " | ".join(s.address), 7)
        cv.text(LEFT, 23, f"GSTIN/UIN: {s.gstin}    State Code: {s.state_code}", 7.5)
        cv.text(RIGHT, 14, "GST INVOICE", 11, bold=True, align="R")
        cv.line(LEFT, 26, RIGHT, 26, 0.6)
        y = 31.0
        left = [
            f"Inv. No.  : {inv.number}",
            f"Inv. Date : {inv.date.strftime(self.date_fmt)}",
            f"POS       : {inv.place_of_supply}-{STATES[inv.place_of_supply][0]}",
        ]
        b = inv.buyer
        right = [
            f"Buyer : {b.name}",
            f"        {', '.join(b.address)}",
            f"GSTIN : {b.gstin}",
        ]
        for i, (a, c) in enumerate(zip(left, right, strict=True)):
            cv.text(LEFT, y + 4.2 * i, a, 7.5)
            cv.text(80, y + 4.2 * i, c, 7.5)
        y += 14
        if inv.e_invoice:
            cv.qr(RIGHT - 20, 29, 20, inv.qr_payload)
            y = max(y, 52)
            y = self.irn_block(cv, LEFT, y - 2, inv, 6.5)
        cv.line(LEFT, y, RIGHT, y, 0.3)
        return y + 1

    def columns(self, inv):
        tc = tax_cols(inv, 18)
        desc = 186 - 7 - 14 - 22 - 22 - 20 - 10 - 18 * len(tc)
        return [
            Col("Sr", 7),
            Col("Particulars", desc),
            Col("HSN", 14),
            Col("Qty", 22, "R"),
            Col("Rate", 22, "R"),
            Col("Taxable", 20, "R"),
            Col("GST%", 10, "R"),
            *tc,
        ]

    def row(self, inv, i, ln):
        m = self.money
        rate = m(ln.unit_price)
        if ln.discount:
            rate += "*"
        return [
            str(i),
            ln.description,
            ln.hsn,
            f"{qty(ln.quantity)} {ln.unit}",
            rate,
            m(ln.taxable_value),
            rate_str(ln.rate).rstrip("%"),
            *tax_vals(inv, ln, m),
        ]

    def totals_height(self, inv):
        return 45

    def totals(self, cv, inv, y):
        if any(ln.discount for ln in inv.lines):
            disc = sum((ln.discount for ln in inv.lines), Decimal(0))
            cv.text(LEFT, y + 1, f"* rate before discount; total discount {plain(disc)}", 6.5)
        y = self.summary_box(cv, RIGHT - 60, y + 5, inv, w=60, size=8)
        self.bank_block(cv, LEFT, y - 22, inv, 7)
        cv.text(LEFT, y + 2, amount_in_words(inv.grand_total), 7.5)
        cv.text(LEFT, y + 7, "E. & O.E.  Subject to local jurisdiction.", 6.5)
        return y + 10


# ---------- 4. ledger (boxed, e-invoice style) ----------


class Ledger(Layout):
    name = "ledger"
    size = 7.5
    row_h = 6.0
    grid = True
    header_fill = 240
    date_fmt = "%d-%b-%Y"
    money = staticmethod(group_indian)

    def header(self, cv, inv):
        cv.rect(LEFT, 10, RIGHT - LEFT, 10, fill=None)
        title = "e-Invoice / Tax Invoice" if inv.e_invoice else "Tax Invoice"
        cv.text(PAGE_W / 2, 17, title, 12, bold=True, align="C")
        top = 20.0
        if inv.e_invoice:
            cv.rect(LEFT, top, RIGHT - LEFT, 30)
            cv.text(LEFT + 2, top + 5, f"IRN : {inv.irn}", 7)
            cv.text(LEFT + 2, top + 10, f"Ack No. : {inv.ack_no}", 7.5)
            cv.text(LEFT + 2, top + 15, f"Ack Date : {inv.date:%d-%b-%Y}", 7.5)
            cv.qr(RIGHT - 28, top + 1, 28, inv.qr_payload)
            top += 30
        cv.rect(LEFT, top, 93, 38)
        cv.rect(105, top, 93, 38)
        self.party_block(cv, LEFT + 2, top + 5, "Seller", inv.supplier, 7.5)
        rows = [
            ("Invoice No.", inv.number),
            ("Dated", inv.date.strftime(self.date_fmt)),
            ("Place of Supply", state_label(inv.place_of_supply)),
            ("Reverse Charge", "N"),
            ("Mode/Terms", "30 days"),
        ]
        for i, (k, v) in enumerate(rows):
            cv.text(107, top + 5 + 6 * i, k, 7.5)
            cv.text(140, top + 5 + 6 * i, v, 8, bold=True)
        top += 38
        cv.rect(LEFT, top, RIGHT - LEFT, 30)
        self.party_block(cv, LEFT + 2, top + 5, "Buyer (Bill to)", inv.buyer, 7.5)
        return top + 30

    def columns(self, inv):
        return [
            Col("Sl", 8, "C"),
            Col("Description of Goods/Services", 52),
            Col("HSN/SAC", 16),
            Col("Qty", 12, "R"),
            Col("Unit", 11),
            Col("Unit Price", 20, "R"),
            Col("Discount", 17, "R"),
            Col("Taxable Val", 22, "R"),
            Col("Rate", 10, "R"),
            Col("Tax Amt", 18, "R"),
        ]

    def row(self, inv, i, ln):
        m = self.money
        return [
            str(i),
            ln.description,
            ln.hsn,
            qty(ln.quantity),
            ln.unit,
            m(ln.unit_price),
            m(ln.discount),
            m(ln.taxable_value),
            rate_str(ln.rate),
            m(ln.tax),
        ]

    def totals_height(self, inv):
        return 60

    def totals(self, cv, inv, y):
        y -= 3
        cv.rect(LEFT, y, RIGHT - LEFT, 40)
        cv.line(120, y, 120, y + 40)
        yy = y + 6
        for label, val in summary_rows(inv, self.money):
            cv.text(122, yy, label, 8)
            cv.text(RIGHT - 2, yy, val, 8, align="R")
            yy += 5
        cv.text(122, y + 36, "Total Invoice Value", 9, bold=True)
        cv.text(RIGHT - 2, y + 36, self.money(inv.grand_total), 9, bold=True, align="R")
        cv.text(LEFT + 2, y + 6, "Amount chargeable (in words):", 7.5)
        words = amount_in_words(inv.grand_total)
        if len(words) > 60:
            cut = words.rfind(" ", 0, 60)
            cv.text(LEFT + 2, y + 11, words[:cut], 7.5, bold=True)
            cv.text(LEFT + 2, y + 15, words[cut + 1 :], 7.5, bold=True)
        else:
            cv.text(LEFT + 2, y + 11, words, 7.5, bold=True)
        self.bank_block(cv, LEFT + 2, y + 21, inv, 7)
        cv.text(RIGHT, y + 52, f"for {inv.supplier.name}", 8, bold=True, align="R")
        cv.text(RIGHT, y + 58, "Authorised Signatory", 7.5, align="R")
        return y + 60


# ---------- 5. letterhead ----------


class Letterhead(Layout):
    name = "letterhead"
    size = 8.5
    row_h = 6.2
    header_fill = None
    date_fmt = "%d.%m.%Y"
    money = staticmethod(group_indian)

    def header(self, cv, inv):
        s = inv.supplier
        cv.text(PAGE_W / 2, 18, s.name, 17, bold=True, align="C")
        cv.text(PAGE_W / 2, 24, ", ".join(s.address), 8, align="C")
        cv.text(PAGE_W / 2, 28.5, f"{s.email}  |  GSTIN {s.gstin}", 8, align="C")
        cv.line(LEFT, 32, RIGHT, 32, 0.8)
        cv.line(LEFT, 33, RIGHT, 33, 0.2)
        cv.text(PAGE_W / 2, 41, "TAX INVOICE", 11, bold=True, align="C")
        boxes = [
            ("Invoice No.", inv.number),
            ("Date", inv.date.strftime(self.date_fmt)),
            ("Place of Supply", state_label(inv.place_of_supply)),
        ]
        w = (RIGHT - LEFT) / 3
        for i, (k, v) in enumerate(boxes):
            x = LEFT + w * i
            cv.rect(x, 45, w, 12)
            cv.text(x + 2, 49.5, k, 7, color=80)
            cv.text(x + 2, 54.5, v, 9, bold=True)
        y = self.party_block(cv, LEFT, 64, "To,", inv.buyer, 8.5)
        if inv.e_invoice:
            cv.qr(RIGHT - 26, 60, 26, inv.qr_payload)
            y = max(y, 88)
            y = self.irn_block(cv, LEFT, y, inv)
        return y + 2

    def columns(self, inv):
        return [
            Col("S.No", 11, "C"),
            Col("Particulars", 75),
            Col("HSN/SAC", 20),
            Col("Qty", 20, "R"),
            Col("Rate", 30, "R"),
            Col("Amount", 30, "R"),
        ]

    def row(self, inv, i, ln):
        m = self.money
        rate = m(ln.unit_price)
        return [
            str(i),
            ln.description + (f" (less {plain(ln.discount)})" if ln.discount else ""),
            ln.hsn,
            f"{qty(ln.quantity)} {ln.unit}",
            rate,
            m(ln.taxable_value),
        ]

    def totals_height(self, inv):
        return 48 + 5 * len(rate_breakup(inv)) * 2

    def totals(self, cv, inv, y):
        x, w = RIGHT - 80, 80
        cv.text(x, y + 2, "Sub Total", 9)
        cv.text(x + w, y + 2, self.money(inv.taxable_total), 9, align="R")
        y += 7
        for rate, taxable, _tax in rate_breakup(inv):
            if inv.printed_mode == IGST:
                amt = sum((ln.igst for ln in inv.lines if ln.rate == rate), Decimal(0))
                cv.text(x, y, f"IGST @ {rate_str(rate)} on {self.money(taxable)}", 8)
                cv.text(x + w, y, self.money(amt), 8, align="R")
                y += 5
            else:
                head2 = "UTGST" if inv.printed_mode == CGST_UTGST else "SGST"
                half = rate_str(rate / 2)
                c = sum((ln.cgst for ln in inv.lines if ln.rate == rate), Decimal(0))
                s = sum((ln.sgst + ln.utgst for ln in inv.lines if ln.rate == rate), Decimal(0))
                cv.text(x, y, f"CGST @ {half}", 8)
                cv.text(x + w, y, self.money(c), 8, align="R")
                cv.text(x, y + 5, f"{head2} @ {half}", 8)
                cv.text(x + w, y + 5, self.money(s), 8, align="R")
                y += 10
        if inv.round_off:
            cv.text(x, y, "Rounded off", 8)
            cv.text(x + w, y, self.money(inv.round_off), 8, align="R")
            y += 5
        cv.line(x, y - 2.5, x + w, y - 2.5, 0.4)
        cv.text(x, y + 3, "TOTAL", 10, bold=True)
        cv.text(x + w, y + 3, "Rs. " + self.money(inv.grand_total), 10, bold=True, align="R")
        y += 10
        cv.text(LEFT, y, amount_in_words(inv.grand_total), 8, bold=True)
        y = self.bank_block(cv, LEFT, y + 6, inv)
        cv.text(RIGHT, y + 4, "Authorised Signatory", 8, align="R")
        return y + 8


LAYOUTS: dict[str, Layout] = {
    lay.name: lay for lay in (Classic(), Modern(), Compact(), Ledger(), Letterhead())
}
