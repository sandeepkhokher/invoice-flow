"""GSTIN format, check character and state codes (gst-invoice-rules §1)."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from types import MappingProxyType

from app.tax.rates import DATA_DIR

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True, slots=True)
class StateCode:
    code: str
    name: str
    ut_without_legislature: bool = False


@cache
def state_codes() -> Mapping[str, StateCode]:
    with (DATA_DIR / "india_state_codes.json").open(encoding="utf-8") as fh:
        raw = json.load(fh)["codes"]
    return MappingProxyType(
        {
            code: StateCode(code, v["name"], bool(v.get("ut_without_legislature", False)))
            for code, v in raw.items()
        }
    )


def check_character(gstin14: str) -> str:
    """Check character for the first 14 characters of a GSTIN."""
    total = 0
    for i, ch in enumerate(gstin14[:14]):
        product = _ALPHABET.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _ALPHABET[(36 - total % 36) % 36]


def gstin_problem(gstin: str) -> str | None:
    """Why a GSTIN is invalid, or None if it is valid."""
    if not GSTIN_PATTERN.fullmatch(gstin):
        return "does not match the GSTIN format (15 characters: state, PAN, entity, Z, check)"
    if gstin[:2] not in state_codes():
        return f"state code {gstin[:2]} is not a GST state code"
    expected = check_character(gstin)
    if gstin[14] != expected:
        return f"check character is {gstin[14]}, expected {expected}"
    return None


def normalise_gstin(gstin: str) -> str:
    return gstin.strip().upper().replace(" ", "")
