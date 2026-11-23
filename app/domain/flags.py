"""The `flags` table (spec §5.4): problems found on an invoice, with severity and status.

S7 raises the first flag, the exact-duplicate `block` of FR-13. S11 adds validation and
near-duplicate flags, overrides (approval-and-audit §3) and the review screen that shows them.
Raising a flag is an audit event, written in the same transaction (approval-and-audit §5).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.audit.chain import append_event
from app.db import Base
from app.domain.actor import Actor


class FlagSeverity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class FlagStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    OVERRIDDEN = "overridden"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in(column: str, enum: type[StrEnum]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v.value}'" for v in enum) + ")"


class Flag(Base):
    __tablename__ = "flags"
    __table_args__ = (
        CheckConstraint(_in("severity", FlagSeverity), name="ck_flags_severity"),
        CheckConstraint(_in("status", FlagStatus), name="ck_flags_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), nullable=False, index=True)
    # Copied from the invoice so the entity-scoped repository can filter flags directly.
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=FlagStatus.OPEN.value)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    related_invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    override_reason: Mapped[str | None] = mapped_column(Text)
    overridden_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


def raise_flag(
    session: Session,
    actor: Actor,
    *,
    invoice_id: int,
    entity_id: int,
    rule_id: str,
    severity: FlagSeverity,
    message: str,
    related_invoice_id: int | None = None,
) -> Flag:
    """Add an open flag and its `flag.raised` audit event in the caller's transaction."""
    flag = Flag(
        invoice_id=invoice_id,
        entity_id=entity_id,
        rule_id=rule_id,
        severity=FlagSeverity(severity).value,
        status=FlagStatus.OPEN.value,
        message=message,
        related_invoice_id=related_invoice_id,
    )
    session.add(flag)
    session.flush()
    append_event(
        session,
        actor=actor.audit_name,
        action="flag.raised",
        entity_id=entity_id,
        invoice_id=invoice_id,
        data={
            "flag_id": flag.id,
            "rule_id": rule_id,
            "severity": flag.severity,
            "related_invoice_id": related_invoice_id,
        },
    )
    return flag
