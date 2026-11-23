"""The `TaxRegime` interface (spec FR-26). `IndiaGST` is the only implementation in v1."""

from typing import Protocol, runtime_checkable

from app.tax.flags import Flag
from app.tax.invoice import Invoice
from app.tax.rates import RateTable


@runtime_checkable
class TaxRegime(Protocol):
    """A tax regime validates an extracted invoice and exposes the rate table it uses.

    Implementations are pure: no DB, no network, no shared mutable state, so they are safe to
    call from several workers at once.
    """

    @property
    def rate_table(self) -> RateTable: ...

    def validate(self, invoice: Invoice) -> list[Flag]: ...
