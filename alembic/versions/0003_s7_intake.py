"""S7 intake: source-file columns on invoices, and the flags table (FR-13 duplicate block).

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEVERITIES = ("block", "warn", "info")
STATUSES = ("open", "resolved", "overridden")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # `uploaded_by` is a user id without a foreign key, like `audit_events.actor`: the S5
    # migration test pins the invoices foreign keys to the entity one. The audit event
    # `invoice.received` records the uploader as well.
    with op.batch_alter_table("invoices", recreate="always") as batch:
        batch.add_column(sa.Column("file_name", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("stored_name", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("file_type", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("file_size", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("page_count", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("uploaded_by", sa.Integer(), nullable=True))
        batch.create_unique_constraint("uq_invoices_stored_name", ["stored_name"])

    op.create_table(
        "flags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("related_invoice_id", sa.Integer(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("overridden_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("severity", SEVERITIES), name="ck_flags_severity"),
        sa.CheckConstraint(_in("status", STATUSES), name="ck_flags_status"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.ForeignKeyConstraint(["related_invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["overridden_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flags_invoice_id", "flags", ["invoice_id"])
    op.create_index("ix_flags_entity_id", "flags", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_flags_entity_id", table_name="flags")
    op.drop_index("ix_flags_invoice_id", table_name="flags")
    op.drop_table("flags")
    with op.batch_alter_table("invoices", recreate="always") as batch:
        batch.drop_constraint("uq_invoices_stored_name", type_="unique")
        for column in (
            "uploaded_by",
            "page_count",
            "file_size",
            "file_type",
            "stored_name",
            "file_name",
        ):
            batch.drop_column(column)
