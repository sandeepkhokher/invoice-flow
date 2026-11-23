"""The `invoices` table (spec §5.4). Later slices add columns with their own migrations.

`state` is read-only on the model. Only `app/domain/transitions.py` writes the underlying
`_state` attribute, together with the audit event (CLAUDE.md conventions).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.domain.money import Money, MoneyType
from app.domain.states import InvoiceState

STATE_VALUES = tuple(s.value for s in InvoiceState)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint(
            "state IN (" + ", ".join(f"'{v}'" for v in STATE_VALUES) + ")",
            name="ck_invoices_state",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # FK to entities is added when the entities table exists (slice S5).
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    _state: Mapped[str] = mapped_column(
        "state", String(32), nullable=False, index=True, default=InvoiceState.RECEIVED.value
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    supplier_gstin: Mapped[str | None] = mapped_column(String(15), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(64))
    number_norm: Mapped[str | None] = mapped_column(String(64))
    fy: Mapped[str | None] = mapped_column(String(9))
    taxable_value: Mapped[Money | None] = mapped_column("taxable_value_paise", MoneyType())
    tax_total: Mapped[Money | None] = mapped_column("tax_total_paise", MoneyType())
    grand_total: Mapped[Money | None] = mapped_column("grand_total_paise", MoneyType())
    status_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    def __init__(self, **kwargs: object) -> None:
        if "state" in kwargs or "_state" in kwargs:
            raise TypeError("an invoice's state is set only by app.domain.transitions")
        super().__init__(**kwargs)
        self._state = InvoiceState.RECEIVED.value

    @property
    def state(self) -> InvoiceState:
        return InvoiceState(self._state)

    def __repr__(self) -> str:
        return f"<Invoice id={self.id} entity={self.entity_id} state={self._state}>"
