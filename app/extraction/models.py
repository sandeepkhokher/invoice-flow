"""`invoice_fields` (spec §5.4): one row per extracted field, with value, confidence and page.

Line items are stored here too, under indexed names (`lines[0].hsn_sac`), rather than in a
separate `line_items` table: the review screen (S11) edits and re-validates fields one by one,
and one table keeps that uniform. `entity_id` is copied from the invoice so the entity-scoped
repository can filter fields directly, as with flags.

Restricted values (bank account number, IFSC; data-protection §1) are stored masked only
(last 4 characters). Field-level encryption arrives with the vendor master in S8.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

CONFIDENCES = ("high", "medium", "low")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class InvoiceField(Base):
    __tablename__ = "invoice_fields"
    __table_args__ = (
        CheckConstraint(_in("confidence", CONFIDENCES), name="ck_invoice_fields_confidence"),
        CheckConstraint(
            _in("model_confidence", CONFIDENCES), name="ck_invoice_fields_model_confidence"
        ),
        UniqueConstraint("invoice_id", "name", name="uq_invoice_fields_invoice_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str | None] = mapped_column(Text)
    # Merged confidence (FR-21) and the model's own rating before the deterministic checks.
    confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    model_confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    # Comma-separated names of the checks that failed (e.g. "not_in_text_layer").
    failed_checks: Mapped[str | None] = mapped_column(String(200))
    page: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
