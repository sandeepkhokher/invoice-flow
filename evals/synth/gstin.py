"""Synthetic GSTIN and PAN generation (gst-invoice-rules §1, data-protection §7).

Every PAN made here starts with ``ZX``. That prefix is this repo's marker for a fictitious
PAN, so a test can prove the eval set carries no real identifiers. The checksum follows
gst-invoice-rules §1 exactly. The GST library (slice S2) has its own validator; this module
stays separate so the generator does not depend on it.
"""

from __future__ import annotations

import random
import re
import string

CHARS = string.digits + string.ascii_uppercase
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
SYNTHETIC_PAN_PREFIX = "ZX"


def check_char(first14: str) -> str:
    """Check character for the first 14 GSTIN characters (gst-invoice-rules §1)."""
    total = 0
    for i, ch in enumerate(first14):
        product = CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return CHARS[(36 - total % 36) % 36]


def is_valid_gstin(gstin: str) -> bool:
    return bool(GSTIN_RE.match(gstin)) and gstin[14] == check_char(gstin[:14])


def make_pan(rng: random.Random, holder_type: str, name: str) -> str:
    """A fictitious PAN: ``ZX`` + letter + holder type + name initial + 4 digits + letter."""
    initial = next((c for c in name.upper() if c.isalpha()), "A")
    pan = (
        SYNTHETIC_PAN_PREFIX
        + rng.choice(string.ascii_uppercase)
        + holder_type
        + initial
        + f"{rng.randrange(10000):04d}"
        + rng.choice(string.ascii_uppercase)
    )
    assert PAN_RE.match(pan)
    return pan


def make_gstin(state_code: str, pan: str, entity_no: int = 1) -> str:
    first14 = f"{state_code}{pan}{CHARS[entity_no]}Z"
    return first14 + check_char(first14)


def corrupt_gstin(gstin: str, rng: random.Random) -> str:
    """Same GSTIN with a wrong check character (still matches the format regex)."""
    wrong = rng.choice([c for c in CHARS if c != gstin[14]])
    return gstin[:14] + wrong
