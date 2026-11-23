"""S5 auth and entities: entities, gst_registrations, users, role and permission grants,
login sessions; foreign key from invoices.entity_id to entities.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLES = ("clerk", "approver", "accountant", "admin", "auditor")
PERMISSIONS = ("flags:override", "bank_details:view")
INVOICE_FK = "fk_invoices_entity_id_entities"


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=False),
        sa.Column("pan", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pan"),
    )
    op.create_table(
        "gst_registrations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("gstin", sa.String(length=15), nullable=False),
        sa.Column("state_code", sa.String(length=2), nullable=False),
        sa.Column("address", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("gstin"),
    )
    op.create_index("ix_gst_registrations_entity_id", "gst_registrations", ["entity_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failed_logins", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "role_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("tier", sa.Integer(), nullable=True),
        sa.CheckConstraint(_in("role", ROLES), name="ck_role_grants_role"),
        sa.CheckConstraint(
            "(role = 'approver' AND tier IS NOT NULL AND tier >= 1)"
            " OR (role != 'approver' AND tier IS NULL)",
            name="ck_role_grants_tier",
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "entity_id", "role", name="uq_role_grants_user_entity_role"),
    )
    op.create_index("ix_role_grants_entity_id", "role_grants", ["entity_id"])
    op.create_index("ix_role_grants_user_id", "role_grants", ["user_id"])

    op.create_table(
        "permission_grants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("permission", sa.String(length=32), nullable=False),
        sa.CheckConstraint(_in("permission", PERMISSIONS), name="ck_permission_grants_perm"),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "entity_id", "permission", name="uq_permission_grants_user_entity_perm"
        ),
    )
    op.create_index("ix_permission_grants_entity_id", "permission_grants", ["entity_id"])
    op.create_index("ix_permission_grants_user_id", "permission_grants", ["user_id"])

    op.create_table(
        "user_sessions",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    # S1 left invoices.entity_id as a plain integer until this table existed (plan.md S1).
    with op.batch_alter_table("invoices", recreate="always") as batch:
        batch.create_foreign_key(INVOICE_FK, "entities", ["entity_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("invoices", recreate="always") as batch:
        batch.drop_constraint(INVOICE_FK, type_="foreignkey")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")
    op.drop_index("ix_permission_grants_user_id", table_name="permission_grants")
    op.drop_index("ix_permission_grants_entity_id", table_name="permission_grants")
    op.drop_table("permission_grants")
    op.drop_index("ix_role_grants_user_id", table_name="role_grants")
    op.drop_index("ix_role_grants_entity_id", table_name="role_grants")
    op.drop_table("role_grants")
    op.drop_table("users")
    op.drop_index("ix_gst_registrations_entity_id", table_name="gst_registrations")
    op.drop_table("gst_registrations")
    op.drop_table("entities")
