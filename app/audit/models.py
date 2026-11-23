"""The `audit_events` table (spec §5.4)."""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class AuditEvent(Base):
    """One audit event. Rows are inserted once and never updated or deleted.

    The migration adds SQLite triggers that reject UPDATE and DELETE on this table, and
    `app.audit.chain` rejects them at the ORM level.

    `seq` is assigned by the application (previous seq + 1), not autoincremented, so two
    writers racing for the same chain position collide on the primary key instead of
    forking the chain. `at` is an ISO-8601 UTC string, and `data_json` is canonical JSON,
    so the hashed form round-trips through the database exactly.
    """

    __tablename__ = "audit_events"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    at: Mapped[str] = mapped_column(String(40), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True)
    invoice_id: Mapped[int | None] = mapped_column(Integer, index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text)
    data_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
