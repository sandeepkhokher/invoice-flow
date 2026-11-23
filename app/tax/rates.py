"""Versioned GST rate table with effective-from dates (spec FR-27, concern C5).

Rates are data (`data/india_gst_rates.json`), never code. Each slab set applies from its
`effective_from` date until the next set starts. Admin editing and auditing of the table
(FR-27) is a DB concern for a later slice; this module only reads and queries it.
"""

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True, slots=True)
class RateSlabSet:
    effective_from: date
    rates: frozenset[Decimal]


@dataclass(frozen=True, slots=True)
class RateTable:
    slab_sets: tuple[RateSlabSet, ...]  # sorted by effective_from
    last_verified: date | None  # None until someone accountable checks the rates (C5)
    verified_by: str

    def __post_init__(self) -> None:
        dates = [s.effective_from for s in self.slab_sets]
        if not dates:
            raise ValueError("rate table has no slab sets")
        if dates != sorted(dates) or len(set(dates)) != len(dates):
            raise ValueError("slab sets must have distinct, ascending effective_from dates")

    def rates_on(self, on: date) -> frozenset[Decimal]:
        """Valid total GST rates (percent) on a date; empty before the first slab set."""
        current: frozenset[Decimal] = frozenset()
        for slab_set in self.slab_sets:
            if slab_set.effective_from <= on:
                current = slab_set.rates
        return current

    def is_known_rate(self, rate: Decimal, on: date) -> bool:
        return rate in self.rates_on(on)

    @classmethod
    def from_dict(cls, data: dict) -> "RateTable":
        slab_sets = tuple(
            sorted(
                (
                    RateSlabSet(
                        effective_from=date.fromisoformat(s["effective_from"]),
                        rates=frozenset(Decimal(r) for r in s["rates"]),
                    )
                    for s in data["slabs"]
                ),
                key=lambda s: s.effective_from,
            )
        )
        return cls(
            slab_sets=slab_sets,
            last_verified=(
                date.fromisoformat(data["last_verified"]) if data.get("last_verified") else None
            ),
            verified_by=data.get("verified_by", ""),
        )


@cache
def india_seed_rate_table() -> RateTable:
    """The shipped seed table. Cached and immutable, so it is safe to share across workers."""
    with (DATA_DIR / "india_gst_rates.json").open(encoding="utf-8") as fh:
        return RateTable.from_dict(json.load(fh))
