"""Duplicate detection library (FR-30, FR-31, gst-invoice-rules §8)."""

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from app.duplicates import (
    EXACT_FILE,
    EXACT_NUMBER,
    NEAR_AMOUNT_DATE,
    NEAR_NUMBER,
    DuplicateMatch,
    InvoiceRecord,
    Severity,
    edit_distance,
    financial_year,
    financial_year_label,
    find_duplicates,
    match_candidate,
    normalise_invoice_number,
)

SUPPLIER = "27AAPFU0939F1ZV"
OTHER_SUPPLIER = "29AAGCB7383J1Z4"

INVOICE = InvoiceRecord(
    id="inv-new",
    entity_id="ent-a",
    supplier_gstin=SUPPLIER,
    invoice_number="INV-0042",
    invoice_date=date(2025, 6, 15),
    total=Decimal("11800.00"),
    file_hash="hash-new",
)


def candidate(**changes) -> InvoiceRecord:
    """A candidate that differs from INVOICE enough to match nothing, then changed."""
    base = replace(
        INVOICE,
        id="inv-old",
        invoice_number="ZZ-9999",
        invoice_date=date(2025, 10, 1),  # same FY as INVOICE, >30 days apart
        total=Decimal("500.00"),
        file_hash="hash-old",
    )
    return replace(base, **changes)


# --- normalisation (gst-invoice-rules §8) -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("INV-0042", "INV-42"),
        ("inv/42", "INV-42"),
        ("INV 42", "INV-42"),
        ("INV42", "INV-42"),
        ("  inv - 042 ", "INV-42"),
        ("2025-26/0007", "2025-26-7"),
        ("2025/26-7", "2025-26-7"),
        ("000", "0"),
        ("A0B01", "A-0-B-1"),
        ("1-23", "1-23"),
        ("12-3", "12-3"),
    ],
)
def test_normalise_invoice_number(raw, expected):
    assert normalise_invoice_number(raw) == expected


def test_acceptance_examples_normalise_equal():
    assert len({normalise_invoice_number(n) for n in ("INV-0042", "inv/42", "INV 42")}) == 1


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("INV-42", "INV-43"),  # different number
        ("1-23", "12-3"),  # separators are not dropped, so digits don't merge across them
        ("INV-42", "INV-420"),  # only leading zeros are stripped
        ("INV-42", "INX-42"),
    ],
)
def test_normalise_keeps_different_numbers_distinct(a, b):
    assert normalise_invoice_number(a) != normalise_invoice_number(b)


# --- financial year (April to March) ----------------------------------------------------


@pytest.mark.parametrize(
    ("day", "fy", "label"),
    [
        (date(2025, 4, 1), 2025, "2025-26"),
        (date(2026, 3, 31), 2025, "2025-26"),
        (date(2025, 3, 31), 2024, "2024-25"),
        (date(2025, 12, 31), 2025, "2025-26"),
        (date(2026, 1, 1), 2025, "2025-26"),
        (date(1999, 4, 1), 1999, "1999-00"),
    ],
)
def test_financial_year(day, fy, label):
    assert financial_year(day) == fy
    assert financial_year_label(day) == label


@pytest.mark.parametrize(
    ("a", "b", "distance"),
    [("", "", 0), ("INV-42", "INV-42", 0), ("INV-42", "INV-43", 1), ("INV-42", "INV-4", 1),
     ("INV-42", "INV-142", 1), ("INV-42", "INV-24", 2), ("", "ABC", 3)],
)  # fmt: skip
def test_edit_distance(a, b, distance):
    assert edit_distance(a, b) == distance
    assert edit_distance(b, a) == distance


# --- matching rules (table-driven) ------------------------------------------------------

CASES = [
    # (name, candidate changes, expected severity or None, expected rules)
    # Exact: same supplier + normalised number + FY -> block (FR-30)
    ("exact same number", {"invoice_number": "INV-0042"}, Severity.BLOCK, (EXACT_NUMBER,)),
    ("exact after normalising /", {"invoice_number": "inv/42"}, Severity.BLOCK, (EXACT_NUMBER,)),
    ("exact after normalising space", {"invoice_number": "INV 42"}, Severity.BLOCK,
     (EXACT_NUMBER,)),
    ("exact, same FY other end", {"invoice_number": "INV-42", "invoice_date": date(2026, 3, 31)},
     Severity.BLOCK, (EXACT_NUMBER,)),
    ("exact, GSTIN case and spaces", {"invoice_number": "INV-42",
     "supplier_gstin": f" {SUPPLIER.lower()} "}, Severity.BLOCK, (EXACT_NUMBER,)),
    # Exact: same file hash -> block, whatever the other fields (FR-30)
    ("same file hash", {"file_hash": "hash-new"}, Severity.BLOCK, (EXACT_FILE,)),
    ("same file hash, other supplier", {"file_hash": "hash-new", "supplier_gstin": OTHER_SUPPLIER},
     Severity.BLOCK, (EXACT_FILE,)),
    ("same file hash, no GSTIN", {"file_hash": "hash-new", "supplier_gstin": None},
     Severity.BLOCK, (EXACT_FILE,)),
    # Exact plus near rules: block, with every rule that fired listed
    ("exact number and amount/date", {"invoice_number": "INV-42", "invoice_date": date(2025, 6, 20),
     "total": Decimal("11800.00")}, Severity.BLOCK, (EXACT_NUMBER, NEAR_AMOUNT_DATE)),
    ("file and number", {"invoice_number": "INV-42", "file_hash": "hash-new"}, Severity.BLOCK,
     (EXACT_FILE, EXACT_NUMBER)),
    # Near: same supplier, edit distance <= 1 on the normalised number -> warn (FR-31)
    ("near, one substitution", {"invoice_number": "INV-0043"}, Severity.WARN, (NEAR_NUMBER,)),
    ("near, one insertion", {"invoice_number": "INV-142"}, Severity.WARN, (NEAR_NUMBER,)),
    ("near, one deletion", {"invoice_number": "INV-4"}, Severity.WARN, (NEAR_NUMBER,)),
    ("same number, previous FY", {"invoice_number": "INV-42", "invoice_date": date(2025, 3, 31)},
     Severity.WARN, (NEAR_NUMBER,)),
    # Near: same supplier, total within 1% and date within 30 days -> warn (FR-31)
    ("near, same total and date", {"invoice_date": date(2025, 6, 15), "total": Decimal("11800.00")},
     Severity.WARN, (NEAR_AMOUNT_DATE,)),
    ("near, total +1% exactly, 30 days later", {"invoice_date": date(2025, 7, 15),
     "total": Decimal("11918.00")}, Severity.WARN, (NEAR_AMOUNT_DATE,)),
    ("near, total -1% of larger, 30 days earlier", {"invoice_date": date(2025, 5, 16),
     "total": Decimal("11682.00")}, Severity.WARN, (NEAR_AMOUNT_DATE,)),
    ("near, both near rules", {"invoice_number": "INV-43", "invoice_date": date(2025, 6, 16),
     "total": Decimal("11800.00")}, Severity.WARN, (NEAR_NUMBER, NEAR_AMOUNT_DATE)),
    # Not duplicates: just outside the thresholds
    ("edit distance 2", {"invoice_number": "INV-24"}, None, ()),
    ("total just over 1%", {"invoice_date": date(2025, 6, 15), "total": Decimal("11919.20")},
     None, ()),
    ("31 days apart", {"invoice_date": date(2025, 7, 16), "total": Decimal("11800.00")}, None, ()),
    # False positives: different suppliers with the same number, total or date
    ("other supplier, same number", {"invoice_number": "INV-0042",
     "supplier_gstin": OTHER_SUPPLIER}, None, ()),
    ("other supplier, near number", {"invoice_number": "INV-43",
     "supplier_gstin": OTHER_SUPPLIER}, None, ()),
    ("other supplier, same total and date", {"supplier_gstin": OTHER_SUPPLIER,
     "invoice_date": date(2025, 6, 15), "total": Decimal("11800.00")}, None, ()),
    ("other supplier, everything but hash", {"supplier_gstin": OTHER_SUPPLIER,
     "invoice_number": "INV-42", "invoice_date": date(2025, 6, 15), "total": Decimal("11800.00")},
     None, ()),
    # Missing data never matches on that field
    ("candidate has no GSTIN", {"supplier_gstin": None, "invoice_number": "INV-42"}, None, ()),
    ("candidate has blank GSTIN", {"supplier_gstin": "  ", "invoice_number": "INV-42"}, None, ()),
    ("candidate has no number", {"invoice_number": None}, None, ()),
    ("candidate has no hash", {"file_hash": None}, None, ()),
    ("candidate has no date, same number", {"invoice_date": None, "invoice_number": "INV-42"},
     Severity.WARN, (NEAR_NUMBER,)),
    ("candidate has no total", {"invoice_date": date(2025, 6, 15), "total": None}, None, ()),
]  # fmt: skip


@pytest.mark.parametrize(
    ("changes", "severity", "rules"),
    [pytest.param(c, s, r, id=name) for name, c, s, r in CASES],
)
def test_match_candidate(changes, severity, rules):
    match = match_candidate(INVOICE, candidate(**changes))
    if severity is None:
        assert match is None
    else:
        assert match == DuplicateMatch("inv-old", "ent-a", severity, rules)


def test_invoice_without_gstin_matches_only_on_file_hash():
    invoice = replace(INVOICE, supplier_gstin=None)
    unregistered_same_number = candidate(supplier_gstin=None, invoice_number="INV-42")
    assert match_candidate(invoice, unregistered_same_number) is None
    assert match_candidate(invoice, candidate(file_hash="hash-new")).rules == (EXACT_FILE,)


def test_invoice_without_hash_does_not_match_candidate_without_hash():
    assert match_candidate(replace(INVOICE, file_hash=None), candidate(file_hash=None)) is None


def test_invoice_does_not_match_itself():
    assert match_candidate(INVOICE, INVOICE) is None
    assert find_duplicates(INVOICE, [INVOICE]) == []


def test_matching_is_symmetric():
    for name, changes, _severity, _rules in CASES:
        other = candidate(**changes)
        forward = match_candidate(INVOICE, other)
        backward = match_candidate(other, INVOICE)
        assert (forward is None) == (backward is None), name
        if forward is not None:
            assert forward.severity == backward.severity, name


# --- find_duplicates: IDs and entity IDs for the caller (C2) ----------------------------


def test_find_duplicates_returns_ids_and_entities_block_first():
    candidates = [
        candidate(id="near-same-entity", invoice_number="INV-43"),
        candidate(id="other-supplier", supplier_gstin=OTHER_SUPPLIER, invoice_number="INV-42"),
        candidate(id="exact-other-entity", entity_id="ent-b", invoice_number="INV/0042"),
        candidate(id="unrelated"),
    ]
    matches = find_duplicates(INVOICE, candidates)
    assert [(m.candidate_id, m.entity_id, m.severity) for m in matches] == [
        ("exact-other-entity", "ent-b", Severity.BLOCK),
        ("near-same-entity", "ent-a", Severity.WARN),
    ]
    assert matches[0].is_cross_entity(INVOICE.entity_id)
    assert not matches[1].is_cross_entity(INVOICE.entity_id)


def test_match_carries_no_candidate_details():
    match = find_duplicates(INVOICE, [candidate(entity_id="ent-b", invoice_number="INV-42")])[0]
    assert set(DuplicateMatch.__slots__) == {"candidate_id", "entity_id", "severity", "rules"}
    assert match.severity == "block"  # StrEnum, ready for flag severity


def test_find_duplicates_accepts_any_iterable():
    matches = find_duplicates(INVOICE, (c for c in [candidate(invoice_number="INV-42")]))
    assert [m.candidate_id for m in matches] == ["inv-old"]


def test_find_duplicates_empty():
    assert find_duplicates(INVOICE, []) == []
