"""Money: integer paise at rest, `Decimal` in Python. Never binary floating point.

`Money` holds an exact number of paise (1 INR = 100 paise). Construction from rupees
accepts `Decimal`, `int` or a decimal string; binary floats are rejected because they
cannot represent most paise amounts exactly. Values with more than two decimal places are
rejected unless the caller rounds explicitly with `Money.round_from`, so rounding is
always a visible decision (GST arithmetic rounds per line, see gst-invoice-rules).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import Integer
from sqlalchemy.types import TypeDecorator

PAISE_PER_RUPEE = 100
_TWO_PLACES = Decimal("0.01")


def _to_decimal(value: Decimal | int | str) -> Decimal:
    # bool is an int subclass; a float is never acceptable money input.
    if isinstance(value, bool) or not isinstance(value, Decimal | int | str):
        raise TypeError(f"money must be Decimal, int or str, not {type(value).__name__}")
    try:
        result = Decimal(value.strip()) if isinstance(value, str) else Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"not a decimal amount: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"money must be finite: {value!r}")
    return result


@dataclass(frozen=True, order=True, slots=True)
class Money:
    """An exact INR amount stored as integer paise."""

    paise: int

    def __post_init__(self) -> None:
        if isinstance(self.paise, bool) or not isinstance(self.paise, int):
            raise TypeError(f"paise must be int, not {type(self.paise).__name__}")

    # construction -------------------------------------------------------------------
    @classmethod
    def zero(cls) -> Money:
        return cls(0)

    @classmethod
    def of(cls, rupees: Decimal | int | str) -> Money:
        """Exact amount in rupees, e.g. `Money.of("1234.50")`. More than 2 places raises."""
        amount = _to_decimal(rupees)
        if amount != amount.quantize(_TWO_PLACES):
            raise ValueError(f"{rupees!r} has fractions of a paisa; use Money.round_from")
        return cls(int(amount * PAISE_PER_RUPEE))

    @classmethod
    def round_from(cls, rupees: Decimal | int | str, rounding: str = ROUND_HALF_UP) -> Money:
        """Amount in rupees rounded to the nearest paisa (half up unless stated)."""
        amount = _to_decimal(rupees).quantize(_TWO_PLACES, rounding=rounding)
        return cls(int(amount * PAISE_PER_RUPEE))

    @classmethod
    def sum(cls, amounts: Any) -> Money:
        total = 0
        for amount in amounts:
            if not isinstance(amount, Money):
                raise TypeError(f"can only sum Money, not {type(amount).__name__}")
            total += amount.paise
        return cls(total)

    # views --------------------------------------------------------------------------
    @property
    def amount(self) -> Decimal:
        """Rupees as an exact `Decimal` with two places."""
        return (Decimal(self.paise) / PAISE_PER_RUPEE).quantize(_TWO_PLACES)

    def __str__(self) -> str:
        return f"{self.amount}"

    # arithmetic ---------------------------------------------------------------------
    def __add__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise + other.paise)

    def __sub__(self, other: object) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        return Money(self.paise - other.paise)

    def __neg__(self) -> Money:
        return Money(-self.paise)

    def __abs__(self) -> Money:
        return Money(abs(self.paise))

    def __mul__(self, other: object) -> Money:
        # Only whole-number multiples are exact; use `times` for rates.
        if isinstance(other, bool) or not isinstance(other, int):
            return NotImplemented
        return Money(self.paise * other)

    __rmul__ = __mul__

    def times(self, factor: Decimal | int | str, rounding: str = ROUND_HALF_UP) -> Money:
        """Multiply by a rate or quantity, rounding the result to the paisa."""
        return Money.round_from(self.amount * _to_decimal(factor), rounding=rounding)


class MoneyType(TypeDecorator[Money]):
    """SQLAlchemy column type: `Money` in Python, INTEGER paise in the database."""

    impl = Integer
    cache_ok = True

    def process_bind_param(self, value: Money | None, dialect: Any) -> int | None:
        if value is None:
            return None
        if not isinstance(value, Money):
            raise TypeError(f"money columns take Money, not {type(value).__name__}")
        return value.paise

    def process_result_value(self, value: int | None, dialect: Any) -> Money | None:
        return None if value is None else Money(int(value))
