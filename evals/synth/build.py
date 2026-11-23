"""Build the eval set's invoices (data only, no files) from a seed.

The set: 45 base invoices (36 clean, 9 with seeded errors) plus 16 seeded duplicates of
clean originals, 61 in all. Base invoices are spaced so they are not near-duplicates of each
other under FR-31, so every duplicate flag the eval sees is one we seeded.
"""

from __future__ import annotations

import copy
import random
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from evals.synth import catalog
from evals.synth.gstin import corrupt_gstin, make_gstin, make_pan
from evals.synth.layouts import LAYOUTS
from evals.synth.model import (
    CGST_SGST,
    IGST,
    Bank,
    Invoice,
    Line,
    Party,
    edit_distance,
    financial_year,
    normalise_number,
    q2,
)

DEFAULT_SEED = 20261123
N_BASE = 45
N_SUPPLIERS = 14
MAX_LINE_GROSS = 320_000  # keeps amounts inside the printed columns
FIRST_DATE, LAST_DATE = date(2025, 7, 1), date(2026, 10, 31)

# Formats for the 45 base invoices. "multipage" is a digital PDF with enough lines for 2+ pages.
FORMAT_PLAN = (
    ["multipage"] * 5 + ["pdf_digital"] * 21 + ["pdf_scanned"] * 6 + ["png"] * 6 + ["jpeg"] * 7
)
ERROR_PLAN = ["wrong_tax_head"] * 3 + ["bad_gstin"] * 3 + ["arithmetic_off"] * 3
DUPLICATE_PLAN = (
    ["exact"] * 4
    + ["rescan"] * 3
    + ["renumbered"] * 3
    + ["same_amount_date"] * 3
    + ["cross_entity"] * 3
)
# What the duplicate checks (FR-30, FR-31, FR-13) should raise for each kind.
DUPLICATE_SEVERITY = {
    "exact": ("duplicate.exact", "block"),  # identical file hash, and same number
    "rescan": ("duplicate.exact", "block"),  # same supplier + number + FY
    "renumbered": ("duplicate.exact", "block"),  # numbers equal after normalisation
    "same_amount_date": ("duplicate.near", "warn"),  # FR-31(b): total and date
    "cross_entity": ("duplicate.exact", "block"),  # FR-32: across entities
}
ERROR_RULES = {
    "wrong_tax_head": ("gst.tax_head", "block"),
    "bad_gstin": ("gst.gstin_checksum", "block"),
    "arithmetic_off": ("gst.arithmetic", "block"),
}
NUMBER_FORMATS = [
    lambda n, fy: f"INV-{n:04d}",
    lambda n, fy: f"TS/{fy[2:4]}-{fy[5:7]}/{n:04d}",
    lambda n, fy: f"BJ{n:05d}",
    lambda n, fy: f"GST/{n}",
    lambda n, fy: f"{fy[2:4]}{fy[5:7]}-{n:04d}",
]


def renumber(number: str) -> str:
    """The same number written differently: swap `-`/`/` and drop leading zeros."""
    swapped = number.translate(str.maketrans("-/", "/-"))
    out, digits = [], ""
    for ch in swapped + "\0":
        if ch.isdigit():
            digits += ch
            continue
        if digits:
            out.append(digits.lstrip("0") or "0")
            digits = ""
        if ch != "\0":
            out.append(ch)
    return "".join(out)


class _Supplier:
    def __init__(self, party: Party, bank: Bank, layout: str, fmt_idx: int, rng: random.Random):
        self.party = party
        self.bank = bank
        self.layout = layout
        self.fmt_idx = fmt_idx
        self.counter = rng.randrange(20, 400)
        self.numbers: list[str] = []

    def next_number(self, rng: random.Random, d: date) -> str:
        fmt = NUMBER_FORMATS[self.fmt_idx]
        while True:
            self.counter += rng.randrange(13, 97)
            num = fmt(self.counter, financial_year(d))
            norm = normalise_number(num)
            if all(edit_distance(norm, normalise_number(o)) > 1 for o in self.numbers):
                self.numbers.append(num)
                return num


def _address(rng: random.Random, state: str) -> list[str]:
    city = rng.choice(catalog.CITIES[state])
    pin = catalog.PIN_PREFIX[state] + f"{rng.randrange(10000):04d}"
    return [
        f"{rng.randrange(1, 300)}, {rng.choice(catalog.LOCALITIES)} {rng.choice(catalog.STREETS)}",
        f"{city} {pin}",
    ]


def make_entities(rng: random.Random) -> list[Party]:
    """Our buyer registrations: one Party per GSTIN, tagged with its entity key."""
    regs = []
    for key, name, holder, states in catalog.ENTITIES:
        pan = make_pan(rng, holder, name)
        for st in states:
            regs.append(
                Party(
                    name=name,
                    address=_address(rng, st),
                    gstin=make_gstin(st, pan),
                    state_code=st,
                    pan=pan,
                    entity=key,
                )
            )
    return regs


def make_suppliers(rng: random.Random, regs: list[Party]) -> list[_Supplier]:
    layouts = list(LAYOUTS)
    our_states = sorted({r.state_code for r in regs})
    other_states = sorted(set(catalog.STATES) - set(our_states))
    firsts = rng.sample(catalog.NAME_FIRST, N_SUPPLIERS)
    out = []
    for i in range(N_SUPPLIERS):
        form, holder = rng.choice(catalog.LEGAL_FORM)
        name = f"{firsts[i]} {rng.choice(catalog.NAME_SECOND)} {form}"
        # Supplier 0 sits in Chandigarh (UTGST); most others share a state with one of ours.
        if i == 0:
            state = "04"
        elif rng.random() < 0.7:
            state = rng.choice(our_states)
        else:
            state = rng.choice(other_states)
        pan = make_pan(rng, holder, name)
        slug = firsts[i].lower()
        party = Party(
            name=name,
            address=_address(rng, state),
            gstin=make_gstin(state, pan, rng.randrange(1, 4)),
            state_code=state,
            pan=pan,
            email=f"accounts@{slug}.example",
        )
        b = rng.randrange(len(catalog.BANK_NAMES))
        bank = Bank(
            bank_name=catalog.BANK_NAMES[b],
            account_name=name,
            account_number=str(rng.randrange(10**11, 10**14)),
            ifsc=f"{catalog.SYNTHETIC_IFSC_BANK[b]}0{rng.randrange(10**6):06d}",
            branch=catalog.CITIES[state][0],
        )
        out.append(_Supplier(party, bank, layouts[i % len(layouts)], i % 5, rng))
    return out


def _lines(rng: random.Random, d: date, n: int) -> list[Line]:
    lines = []
    for _ in range(n):
        desc, hsn, unit, lo, hi, old, new = rng.choice(catalog.ITEMS)
        if unit in catalog.SERVICE_UNITS:
            quantity = Decimal(1)
        elif hi > 30000:
            quantity = Decimal(rng.randrange(1, 5))
        else:
            quantity = Decimal(rng.randrange(1, 60))
        price = Decimal(rng.randrange(lo, hi)) + rng.choice(
            [Decimal("0"), Decimal("0"), Decimal("0.50"), Decimal("0.25"), Decimal("0.75")]
        )
        quantity = max(Decimal(1), min(quantity, Decimal(MAX_LINE_GROSS // int(price))))
        gross = quantity * price
        discount = Decimal("0.00")
        if gross < 50000 and rng.random() < 0.25:
            discount = q2(gross * rng.choice([2, 5, 10]) / 100)
        rate = Decimal(old if d < catalog.RATE_CHANGE else new)
        lines.append(Line(desc, hsn, quantity, unit, q2(price), discount, rate))
    return lines


def _random_date(rng: random.Random) -> date:
    return FIRST_DATE + timedelta(days=rng.randrange((LAST_DATE - FIRST_DATE).days + 1))


def _near(a: Invoice, b: Invoice) -> bool:
    """FR-31(b): same supplier, totals within 1% and dates within 30 days."""
    if a.supplier.gstin != b.supplier.gstin:
        return False
    hi = max(a.grand_total, b.grand_total)
    return abs(a.grand_total - b.grand_total) <= hi / 100 and abs((a.date - b.date).days) <= 30


def build(seed: int = DEFAULT_SEED) -> tuple[list[Invoice], list[Party]]:
    rng = random.Random(seed)
    regs = make_entities(rng)
    suppliers = make_suppliers(rng, regs)
    formats = FORMAT_PLAN[:]
    rng.shuffle(formats)

    base: list[Invoice] = []
    for i, fmt in enumerate(formats):
        sup = suppliers[i % N_SUPPLIERS] if i < N_SUPPLIERS else rng.choice(suppliers)
        same_state = [r for r in regs if r.state_code == sup.party.state_code]
        if same_state and (sup.party.state_code == "04" or rng.random() < 0.55):
            buyer = rng.choice(same_state)
        else:
            buyer = rng.choice(regs)
        if fmt == "multipage":
            n_lines = rng.randrange(48, 62)
        elif fmt in ("png", "jpeg", "pdf_scanned"):
            n_lines = rng.randrange(1, 7)
        else:
            n_lines = rng.randrange(1, 9)
        while True:
            d = _random_date(rng)
            inv = Invoice(
                id=f"inv-{i + 1:03d}",
                supplier=sup.party,
                buyer=buyer,
                number="",
                date=d,
                lines=_lines(rng, d, n_lines),
                bank=sup.bank,
                layout=sup.layout,
                fmt="pdf_digital" if fmt == "multipage" else fmt,
                rounds=rng.random() < 0.7,
                e_invoice=rng.random() < 0.4,
            )
            inv.compute()
            if not any(_near(inv, o) for o in base):
                break
        inv.number = sup.next_number(rng, d)
        if inv.e_invoice:
            inv.ack_no = f"1120{rng.randrange(10**10, 10**11)}"
        base.append(inv)

    # Seeded errors on single-page base invoices.
    # A wrong tax head is only detectable when some tax is charged, so those errors go on
    # invoices with tax above zero (an all-exempt invoice prints 0.00 under every head).
    candidates = [inv for inv in base if len(inv.lines) < 10]
    taxed = [inv for inv in candidates if inv.tax_total > 0]
    head_errors = [k for k in ERROR_PLAN if k == "wrong_tax_head"]
    other_errors = [k for k in ERROR_PLAN if k != "wrong_tax_head"]
    on_taxed = rng.sample(taxed, len(head_errors))
    rest = rng.sample([inv for inv in candidates if inv not in on_taxed], len(other_errors))
    for inv, kind in zip(on_taxed + rest, head_errors + other_errors, strict=True):
        _seed_error(inv, kind, rng)

    # Duplicates of clean, single-page originals (one duplicate per original).
    clean = [inv for inv in base if inv.seeded_error is None and len(inv.lines) < 10]
    originals = rng.sample(clean, len(DUPLICATE_PLAN))
    dups = []
    for j, (orig, kind) in enumerate(zip(originals, DUPLICATE_PLAN, strict=True)):
        dup = _duplicate(orig, kind, f"inv-{N_BASE + j + 1:03d}", rng, regs, suppliers)
        dups.append(dup)
    return base + dups, regs


def _seed_error(inv: Invoice, kind: str, rng: random.Random) -> None:
    rule, severity = ERROR_RULES[kind]
    inv.seeded_error = kind
    inv.error_detail = {"rule": rule, "expected_severity": severity}
    if kind == "wrong_tax_head":
        correct = inv.correct_mode
        inv.printed_mode = CGST_SGST if correct == IGST else IGST
        inv.compute()
        inv.error_detail["correct_heads"] = correct
    elif kind == "bad_gstin":
        valid = inv.supplier.gstin
        inv.supplier = replace(inv.supplier, gstin=corrupt_gstin(valid, rng))
        inv.error_detail["valid_gstin"] = valid
    elif kind == "arithmetic_off":
        delta = rng.choice([Decimal("1.01"), Decimal("10.00"), Decimal("100.00")])
        correct = inv.grand_total
        inv.grand_total = correct + delta
        inv.error_detail["correct_grand_total"] = str(correct)
        inv.error_detail["difference"] = str(delta)


def _duplicate(orig, kind, new_id, rng, regs, suppliers) -> Invoice:
    dup = copy.deepcopy(orig)
    dup.id = new_id
    dup.duplicate_of = orig.id
    dup.duplicate_kind = kind
    dup.scan = None
    if kind == "rescan":
        dup.fmt = rng.choice(["png", "jpeg", "pdf_scanned"])
    elif kind == "renumbered":
        dup.number = renumber(orig.number)
    elif kind == "same_amount_date":
        sup = next(s for s in suppliers if s.party.gstin == orig.supplier.gstin)
        dup.number = sup.next_number(rng, orig.date)
        if dup.e_invoice:
            dup.ack_no = f"1120{rng.randrange(10**10, 10**11)}"
    elif kind == "cross_entity":
        others = [r for r in regs if r.entity != orig.buyer.entity]
        dup.buyer = rng.choice(others)
        dup.printed_mode = ""
        dup.compute()
    return dup
