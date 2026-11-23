"""Alembic migration for invoices, audit_events and jobs (ticket #6 AC6)."""

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from alembic import command
from app import models  # noqa: F401  (registers the tables)
from app.db import Base
from tests.conftest import alembic_config


def test_single_head():
    cfg = alembic_config("sqlite://")
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1


def test_upgrade_creates_tables_and_triggers(engine):
    insp = inspect(engine)
    assert {"invoices", "audit_events", "jobs"} <= set(insp.get_table_names())
    with engine.connect() as conn:
        triggers = {
            r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
        }
    assert triggers == {"audit_events_no_update", "audit_events_no_delete"}


def test_migration_matches_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_downgrade_and_upgrade_again(engine, settings):
    cfg = alembic_config(settings.database_url)
    command.downgrade(cfg, "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    command.upgrade(cfg, "head")
    assert "audit_events" in inspect(engine).get_table_names()


def test_s5_tables_and_invoice_entity_foreign_key(engine):
    insp = inspect(engine)
    assert {
        "entities",
        "gst_registrations",
        "users",
        "role_grants",
        "permission_grants",
        "user_sessions",
    } <= set(insp.get_table_names())
    fks = insp.get_foreign_keys("invoices")
    assert [(fk["constrained_columns"], fk["referred_table"]) for fk in fks] == [
        (["entity_id"], "entities")
    ]
    # Rebuilding invoices for the FK kept S1's state check constraint.
    checks = {c["name"] for c in insp.get_check_constraints("invoices")}
    assert "ck_invoices_state" in checks
    assert {"gstin"} in [
        set(u["column_names"]) for u in insp.get_unique_constraints("gst_registrations")
    ]


def test_downgrade_s5_keeps_s1_schema(engine, settings):
    cfg = alembic_config(settings.database_url)
    command.downgrade(cfg, "0001")
    insp = inspect(engine)
    assert "entities" not in insp.get_table_names()
    assert insp.get_foreign_keys("invoices") == []
    assert "ck_invoices_state" in {c["name"] for c in insp.get_check_constraints("invoices")}
    command.upgrade(cfg, "head")
