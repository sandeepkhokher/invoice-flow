"""Append-only, hash-chained audit trail and `make verify-audit` (ticket #6 AC4, AC5)."""

import hashlib
import json
import os
import sqlite3
import subprocess

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError

from app.audit import (
    GENESIS_HASH,
    AppendOnlyViolation,
    AuditEvent,
    append_event,
    canonical_json,
    verify_chain,
)
from app.audit.verify import main as verify_main
from tests.conftest import REPO_ROOT


def write_events(session, n=3):
    for i in range(n):
        append_event(
            session,
            actor="user:1",
            action="test.event",
            entity_id=1,
            invoice_id=i,
            data={"i": i, "note": "₹ café"},
        )
    session.commit()


def raw_db(settings) -> sqlite3.Connection:
    """Direct file access, as an attacker with the DB file would have."""
    return sqlite3.connect(settings.data_dir / "invoice_flow.db")


def tamper(settings, sql, params=()):
    conn = raw_db(settings)
    conn.execute("DROP TRIGGER audit_events_no_update")
    conn.execute("DROP TRIGGER audit_events_no_delete")
    conn.execute(sql, params)
    conn.commit()
    conn.close()


# --- hash chain ------------------------------------------------------------------------


def test_events_store_prev_hash_and_sha256_over_canonical_json(session):
    write_events(session)
    events = session.scalars(select(AuditEvent).order_by(AuditEvent.seq)).all()
    assert [e.seq for e in events] == [1, 2, 3]
    assert events[0].prev_hash == GENESIS_HASH
    for prev, cur in zip(events, events[1:], strict=False):
        assert cur.prev_hash == prev.hash
    for e in events:
        body = {
            "seq": e.seq,
            "at": e.at,
            "actor": e.actor,
            "entity_id": e.entity_id,
            "invoice_id": e.invoice_id,
            "action": e.action,
            "reason": e.reason,
            "data": json.loads(e.data_json),
            "prev_hash": e.prev_hash,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        assert e.hash == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert e.at.endswith("+00:00")  # UTC


def test_canonical_json_is_deterministic_and_rejects_floats():
    assert canonical_json({"b": 1, "a": [True, None, "x"]}) == '{"a":[true,null,"x"],"b":1}'
    with pytest.raises(TypeError):
        canonical_json({"amount": 1.5})
    with pytest.raises(TypeError):
        canonical_json({"nested": [{"amount": 0.1}]})
    with pytest.raises(TypeError):
        canonical_json({1: "non-string key"})


def test_intact_chain_verifies(session):
    write_events(session, 5)
    result = verify_chain(session)
    assert result.ok and result.checked == 5


def test_empty_chain_verifies(session):
    assert verify_chain(session).ok


def test_racing_writers_cannot_fork_the_chain(sessions):
    with sessions() as a, sessions() as b:
        append_event(a, actor="user:1", action="test.a")
        a.commit()
        # b computed its position before a committed: simulate by inserting at the same seq.
        b.add(
            AuditEvent(
                seq=1, at="x", actor="user:2", action="test.b", data_json="{}",
                prev_hash=GENESIS_HASH, hash="f" * 64,
            )
        )  # fmt: skip
        with pytest.raises(IntegrityError):
            b.commit()


# --- append-only -------------------------------------------------------------------------


def test_orm_update_is_refused(session):
    write_events(session, 1)
    ev = session.scalars(select(AuditEvent)).one()
    ev.action = "edited"
    with pytest.raises(AppendOnlyViolation):
        session.flush()


def test_orm_delete_is_refused(session):
    write_events(session, 1)
    session.delete(session.scalars(select(AuditEvent)).one())
    with pytest.raises(AppendOnlyViolation):
        session.flush()


@pytest.mark.parametrize(
    "stmt",
    [
        update(AuditEvent).values(action="x"),
        delete(AuditEvent),
        update(AuditEvent.__table__).values(action="x"),
        delete(AuditEvent.__table__),
    ],
)
def test_bulk_update_and_delete_are_refused(session, stmt):
    write_events(session, 1)
    with pytest.raises(AppendOnlyViolation):
        session.execute(stmt)


@pytest.mark.parametrize(
    "sql", ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"]
)
def test_database_triggers_refuse_raw_sql(session, settings, sql):
    write_events(session, 2)
    with pytest.raises(Exception, match="append-only"):
        session.execute(text(sql))
    session.rollback()
    conn = raw_db(settings)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(sql)
    conn.close()
    assert verify_chain(session).checked == 2


def test_app_has_no_update_or_delete_path_for_audit_events():
    """No application code issues UPDATE or DELETE against audit_events."""
    offenders = []
    for path in (REPO_ROOT / "app").rglob("*.py"):
        src = path.read_text()
        for needle in ("update(AuditEvent", "delete(AuditEvent", "UPDATE audit_events",
                       "DELETE FROM audit_events"):  # fmt: skip
            if needle in src:
                offenders.append(f"{path.name}: {needle}")
    assert offenders == []


# --- tamper detection ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sql", "params", "bad_seq"),
    [
        ("UPDATE audit_events SET data_json = ? WHERE seq = 2", ('{"i":99}',), 2),
        ("UPDATE audit_events SET actor = 'user:666' WHERE seq = 3", (), 3),
        ("UPDATE audit_events SET reason = 'backdated' WHERE seq = 1", (), 1),
        ("DELETE FROM audit_events WHERE seq = 2", (), 3),
    ],
)
def test_tampered_row_breaks_the_chain(session, settings, sql, params, bad_seq):
    write_events(session, 4)
    tamper(settings, sql, params)
    result = verify_chain(session)
    assert not result.ok
    assert result.breaks[0].seq == bad_seq


def test_recomputed_hash_still_breaks_the_next_link(session, settings):
    """Rewriting a row and its own hash is caught by the following event's prev_hash."""
    write_events(session, 3)
    tamper(settings, "UPDATE audit_events SET hash = ? WHERE seq = 2", ("a" * 64,))
    result = verify_chain(session)
    assert [b.seq for b in result.breaks] == [2, 3]


# --- `make verify-audit` -------------------------------------------------------------------


def test_verify_cli_passes_on_intact_chain(session, settings, capsys):
    write_events(session, 3)
    assert verify_main(["--database-url", settings.database_url]) == 0
    assert "audit chain OK: 3 events verified" in capsys.readouterr().out


def test_verify_cli_fails_on_tampered_row(session, settings, capsys):
    write_events(session, 3)
    tamper(settings, "UPDATE audit_events SET data_json = '{}' WHERE seq = 2")
    assert verify_main(["--database-url", settings.database_url]) == 1
    out = capsys.readouterr().out
    assert "BROKEN" in out and "seq 2" in out
    assert "café" not in out  # reports positions, never event data


def test_verify_cli_on_unmigrated_database(tmp_path, capsys):
    assert verify_main(["--database-url", f"sqlite:///{tmp_path / 'empty.db'}"]) == 2
    assert verify_main(["--database-url", f"sqlite:///{tmp_path / 'no' / 'dir.db'}"]) == 2


def _make_verify_audit(settings):
    env = {**os.environ, "INVOICE_FLOW_DATA_DIR": str(settings.data_dir)}
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(
        ["make", "verify-audit"], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
        timeout=120,
    )  # fmt: skip


def test_make_verify_audit(session, settings):
    write_events(session, 3)
    ok = _make_verify_audit(settings)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert "audit chain OK: 3 events verified" in ok.stdout

    tamper(settings, "UPDATE audit_events SET action = 'invoice.approve' WHERE seq = 1")
    bad = _make_verify_audit(settings)
    assert bad.returncode != 0
    assert "audit chain BROKEN" in bad.stdout
