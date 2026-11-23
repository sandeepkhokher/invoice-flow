"""S9 extraction: invoice_fields, extracted header columns, per-entity touch-free setting.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-24
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONFIDENCES = ("high", "medium", "low")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    with op.batch_alter_table("invoices") as batch:
        batch.add_column(sa.Column("supplier_name", sa.String(length=200), nullable=True))
        batch.add_column(sa.Column("invoice_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("irn", sa.String(length=64), nullable=True))
        batch.add_column(
            sa.Column("auto_reviewed", sa.Boolean(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("entities") as batch:
        batch.add_column(sa.Column("touch_free", sa.Boolean(), nullable=False, server_default="1"))

    op.create_table(
        "invoice_fields",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("confidence", sa.String(length=8), nullable=False),
        sa.Column("model_confidence", sa.String(length=8), nullable=False),
        sa.Column("failed_checks", sa.String(length=200), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("confidence", CONFIDENCES), name="ck_invoice_fields_confidence"),
        sa.CheckConstraint(
            _in("model_confidence", CONFIDENCES), name="ck_invoice_fields_model_confidence"
        ),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("invoice_id", "name", name="uq_invoice_fields_invoice_name"),
    )
    op.create_index("ix_invoice_fields_invoice_id", "invoice_fields", ["invoice_id"])
    op.create_index("ix_invoice_fields_entity_id", "invoice_fields", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_invoice_fields_entity_id", table_name="invoice_fields")
    op.drop_index("ix_invoice_fields_invoice_id", table_name="invoice_fields")
    op.drop_table("invoice_fields")
    with op.batch_alter_table("entities") as batch:
        batch.drop_column("touch_free")
    with op.batch_alter_table("invoices") as batch:
        for column in ("auto_reviewed", "irn", "invoice_date", "supplier_name"):
            batch.drop_column(column)
