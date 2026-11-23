"""Duplicate invoice detection (spec FR-30, FR-31; gst-invoice-rules §8).

A pure library: no database, no I/O, no shared state. The caller loads candidate records
(for FR-32, every invoice of every entity with the same supplier GSTIN, plus any with the same
file hash) and passes them in. Matches carry only the candidate's ID and entity ID, so the
caller decides what to show; a match in an entity the user cannot see must be shown only as
"possible duplicate in another entity" (spec concern C2).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

# Rule IDs, carried on every match so flags can cite the rule that fired.
EXACT_NUMBER = "DUP-EXACT-NUMBER"  # same supplier GSTIN + normalised number + FY
EXACT_FILE = "DUP-EXACT-FILE"  # same file hash
NEAR_NUMBER = "DUP-NEAR-NUMBER"  # same supplier, normalised numbers within edit distance 1
NEAR_AMOUNT_DATE = "DUP-NEAR-AMOUNT-DATE"  # same supplier, total within 1%, date within 30 days

NEAR_MAX_EDIT_DISTANCE = 1
NEAR_TOTAL_TOLERANCE = Decimal("0.01")  # 1%
NEAR_MAX_DAYS = 30

_TOKEN = re.compile(r"[0-9]+|[^0-9\-/]+")


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"


@dataclass(frozen=True, slots=True)
class InvoiceRecord:
    """The fields duplicate detection needs. Any of them may be missing (None)."""

    id: str
    entity_id: str
    supplier_gstin: str | None
    invoice_number: str | None
    invoice_date: date | None
    total: Decimal | None
    file_hash: str | None = None


@dataclass(frozen=True, slots=True)
class DuplicateMatch:
    """One candidate that matched. Holds IDs only; the caller resolves (or hides) details."""

    candidate_id: str
    entity_id: str
    severity: Severity
    rules: tuple[str, ...]

    def is_cross_entity(self, entity_id: str) -> bool:
        return self.entity_id != entity_id


def normalise_invoice_number(number: str) -> str:
    """Normalise per gst-invoice-rules §8.

    Uppercase, strip whitespace, treat `-` and `/` as the same separator and strip leading
    zeros from numeric runs. A change between letters and digits counts as a separator, so
    `INV-0042`, `inv/42`, `INV 42` and `INV42` all normalise to `INV-42`, while `1-23` and
    `12-3` stay distinct.
    """
    compact = re.sub(r"\s+", "", number).upper()
    tokens = []
    for token in _TOKEN.findall(compact):
        if token.isdigit():
            token = token.lstrip("0") or "0"
        tokens.append(token)
    return "-".join(tokens)


def financial_year(day: date) -> int:
    """Indian financial year (April to March), as its starting calendar year.

    1 Apr 2025 to 31 Mar 2026 is FY 2025 (written 2025-26).
    """
    return day.year if day.month >= 4 else day.year - 1


def financial_year_label(day: date) -> str:
    start = financial_year(day)
    return f"{start}-{(start + 1) % 100:02d}"


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (insertions, deletions and substitutions each cost 1)."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def totals_within_tolerance(a: Decimal, b: Decimal) -> bool:
    """True when the totals differ by at most 1% of the larger of the two."""
    return abs(a - b) <= NEAR_TOTAL_TOLERANCE * max(abs(a), abs(b))


def _gstin(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def _number(value: str | None) -> str | None:
    if value is None:
        return None
    normalised = normalise_invoice_number(value)
    return normalised or None


def match_candidate(invoice: InvoiceRecord, candidate: InvoiceRecord) -> DuplicateMatch | None:
    """Compare one invoice with one candidate. Returns None when no rule fires."""
    if candidate.id == invoice.id:
        return None

    block: list[str] = []
    warn: list[str] = []

    if invoice.file_hash and invoice.file_hash == candidate.file_hash:
        block.append(EXACT_FILE)

    supplier = _gstin(invoice.supplier_gstin)
    same_supplier = supplier is not None and supplier == _gstin(candidate.supplier_gstin)
    if same_supplier:
        number, other_number = _number(invoice.invoice_number), _number(candidate.invoice_number)
        numbers_known = number is not None and other_number is not None
        dates_known = invoice.invoice_date is not None and candidate.invoice_date is not None

        if (
            numbers_known
            and dates_known
            and number == other_number
            and financial_year(invoice.invoice_date) == financial_year(candidate.invoice_date)
        ):
            block.append(EXACT_NUMBER)
        elif numbers_known and edit_distance(number, other_number) <= NEAR_MAX_EDIT_DISTANCE:
            warn.append(NEAR_NUMBER)

        if (
            dates_known
            and invoice.total is not None
            and candidate.total is not None
            and abs((invoice.invoice_date - candidate.invoice_date).days) <= NEAR_MAX_DAYS
            and totals_within_tolerance(invoice.total, candidate.total)
        ):
            warn.append(NEAR_AMOUNT_DATE)

    if block:
        return DuplicateMatch(
            candidate.id, candidate.entity_id, Severity.BLOCK, tuple(block + warn)
        )
    if warn:
        return DuplicateMatch(candidate.id, candidate.entity_id, Severity.WARN, tuple(warn))
    return None


def find_duplicates(
    invoice: InvoiceRecord, candidates: Iterable[InvoiceRecord]
) -> list[DuplicateMatch]:
    """All candidates that match, `block` matches first, then in the order given."""
    matches = [m for c in candidates if (m := match_candidate(invoice, c)) is not None]
    return sorted(matches, key=lambda m: m.severity != Severity.BLOCK)
