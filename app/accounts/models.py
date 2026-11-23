"""Entities, GST registrations, users, per-entity grants and login sessions (spec §2, §5.4).

FR-1: an entity (legal name, PAN) has one or more GST registrations; a GSTIN is unique
across all entities. FR-3: roles and extra permissions are granted per entity.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.domain.actor import Permission, Role

ROLE_VALUES = tuple(r.value for r in Role)
PERMISSION_VALUES = tuple(p.value for p in Permission)


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """SQLite hands back naive datetimes; every stored datetime is UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Entity(Base):
    __tablename__ = "entities"
    # EntityScope (app/scoping.py) filters this table on its own id.
    __entity_column__ = "id"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    pan: Mapped[str] = mapped_column(String(10), nullable=False, unique=True)
    # FR-23 / C8: invoices with every field high confidence and no block or warn flags skip
    # review and go straight to ready_for_approval, marked auto-reviewed. Default on.
    touch_free: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    registrations: Mapped[list[GstRegistration]] = relationship(
        back_populates="entity", order_by="GstRegistration.gstin"
    )

    def __repr__(self) -> str:
        return f"<Entity id={self.id}>"


class GstRegistration(Base):
    __tablename__ = "gst_registrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    # Unique across all entities: a GSTIN cannot belong to two entities (FR-1).
    gstin: Mapped[str] = mapped_column(String(15), nullable=False, unique=True)
    state_code: Mapped[str] = mapped_column(String(2), nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    entity: Mapped[Entity] = relationship(back_populates="registrations")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def __repr__(self) -> str:  # never the email or hash
        return f"<User id={self.id}>"


class RoleGrant(Base):
    """One role for one user on one entity. Approvers carry their tier (1..n)."""

    __tablename__ = "role_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_id", "role", name="uq_role_grants_user_entity_role"),
        CheckConstraint(_in("role", ROLE_VALUES), name="ck_role_grants_role"),
        CheckConstraint(
            "(role = 'approver' AND tier IS NOT NULL AND tier >= 1)"
            " OR (role != 'approver' AND tier IS NULL)",
            name="ck_role_grants_tier",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer)


class PermissionGrant(Base):
    """An extra permission (`flags:override`, `bank_details:view`) on one entity."""

    __tablename__ = "permission_grants"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "entity_id", "permission", name="uq_permission_grants_user_entity_perm"
        ),
        CheckConstraint(_in("permission", PERMISSION_VALUES), name="ck_permission_grants_perm"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("entities.id"), nullable=False, index=True)
    permission: Mapped[str] = mapped_column(String(32), nullable=False)


class UserSession(Base):
    """A server-side login session (data-protection §3).

    The cookie holds a random token; only its SHA-256 is stored, so a copy of the database
    cannot be replayed as a login. `last_seen_at` drives the 30-minute idle timeout.
    """

    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)
    # The entity picked in the header switcher; NULL means "All my entities".
    entity_id: Mapped[int | None] = mapped_column(ForeignKey("entities.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
