"""S1 domain core: invoices, audit_events (append-only, hash-chained), jobs.

Revision ID: 0001
Revises:
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATES = (
    "received",
    "extracting",
    "extraction_failed",
    "needs_review",
    "ready_for_approval",
    "in_approval",
    "rejected",
    "approved",
    "exported",
    "paid",
    "discarded",
)
JOB_STATUSES = ("queued", "running", "done", "failed")


def _in(column: str, values: Sequence[str]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("at", sa.String(length=40), nullable=False),
        sa.Column("actor", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("data_json", sa.Text(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("hash"),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])
    op.create_index("ix_audit_events_entity_id", "audit_events", ["entity_id"])
    op.create_index("ix_audit_events_invoice_id", "audit_events", ["invoice_id"])
    # Append-only at the database level (approval-and-audit §5).
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=True),
        sa.Column("supplier_gstin", sa.String(length=15), nullable=True),
        sa.Column("invoice_number", sa.String(length=64), nullable=True),
        sa.Column("number_norm", sa.String(length=64), nullable=True),
        sa.Column("fy", sa.String(length=9), nullable=True),
        # money: integer paise
        sa.Column("taxable_value_paise", sa.Integer(), nullable=True),
        sa.Column("tax_total_paise", sa.Integer(), nullable=True),
        sa.Column("grand_total_paise", sa.Integer(), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("state", STATES), name="ck_invoices_state"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_invoices_entity_id", "invoices", ["entity_id"])
    op.create_index("ix_invoices_file_hash", "invoices", ["file_hash"])
    op.create_index("ix_invoices_state", "invoices", ["state"])
    op.create_index("ix_invoices_supplier_gstin", "invoices", ["supplier_gstin"])

    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("invoice_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("status", JOB_STATUSES), name="ck_jobs_status"),
        sa.ForeignKeyConstraint(["invoice_id"], ["invoices.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jobs_invoice_id", "jobs", ["invoice_id"])
    op.create_index("ix_jobs_status_locked_until", "jobs", ["status", "locked_until"])


def downgrade() -> None:
    op.drop_index("ix_jobs_status_locked_until", table_name="jobs")
    op.drop_index("ix_jobs_invoice_id", table_name="jobs")
    op.drop_table("jobs")
    op.drop_index("ix_invoices_supplier_gstin", table_name="invoices")
    op.drop_index("ix_invoices_state", table_name="invoices")
    op.drop_index("ix_invoices_file_hash", table_name="invoices")
    op.drop_index("ix_invoices_entity_id", table_name="invoices")
    op.drop_table("invoices")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    op.drop_index("ix_audit_events_invoice_id", table_name="audit_events")
    op.drop_index("ix_audit_events_entity_id", table_name="audit_events")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_table("audit_events")
