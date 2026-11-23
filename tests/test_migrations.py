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
