"""Writing and verifying the audit hash chain.

Each event's `hash` is SHA-256 over the canonical JSON of its fields plus `prev_hash`, the
previous event's hash (the first event uses `GENESIS_HASH`). Changing, inserting or
deleting a row in the middle of the chain breaks every later link, which `verify_chain`
reports. Events are written with `append_event` inside the caller's transaction, so they
commit or roll back together with the change they describe.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, event, select
from sqlalchemy.orm import Session, UOWTransaction

from app.audit.models import AuditEvent

GENESIS_HASH = "0" * 64


class AppendOnlyViolation(RuntimeError):
    """Raised when code tries to update or delete an audit event."""


@dataclass(frozen=True)
class ChainBreak:
    seq: int
    problem: str


@dataclass(frozen=True)
class VerifyResult:
    checked: int
    breaks: tuple[ChainBreak, ...]

    @property
    def ok(self) -> bool:
        return not self.breaks


def _reject_float(value: Any) -> Any:
    raise TypeError(f"audit data must be JSON-native without floats, got {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace, UTF-8, no floats (they don't round-trip)."""

    def check(v: Any) -> None:
        if isinstance(v, float):
            _reject_float(v)
        elif isinstance(v, dict):
            for key, item in v.items():
                if not isinstance(key, str):
                    raise TypeError("audit data keys must be strings")
                check(item)
        elif isinstance(v, list | tuple):
            for item in v:
                check(item)

    check(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_reject_float
    )


def _hashed_fields(
    *,
    seq: int,
    at: str,
    actor: str,
    entity_id: int | None,
    invoice_id: int | None,
    action: str,
    reason: str | None,
    data: Any,
    prev_hash: str,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "at": at,
        "actor": actor,
        "entity_id": entity_id,
        "invoice_id": invoice_id,
        "action": action,
        "reason": reason,
        "data": data,
        "prev_hash": prev_hash,
    }


def event_hash(**fields: Any) -> str:
    """SHA-256 hex digest over the canonical JSON of an event's fields and `prev_hash`."""
    payload = canonical_json(_hashed_fields(**fields)).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def append_event(
    session: Session,
    *,
    actor: str,
    action: str,
    entity_id: int | None = None,
    invoice_id: int | None = None,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> AuditEvent:
    """Add the next event to the chain in the session's current transaction (no commit).

    Concurrent writers that read the same chain tail collide on `seq` (primary key) at
    flush or commit time; the loser's whole transaction fails and must be retried.
    """
    data = dict(data or {})
    data_json = canonical_json(data)
    last = session.execute(
        select(AuditEvent.seq, AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)
    ).first()
    seq = 1 if last is None else last.seq + 1
    prev_hash = GENESIS_HASH if last is None else last.hash
    at = utc_now_iso()
    fields = dict(
        seq=seq,
        at=at,
        actor=actor,
        entity_id=entity_id,
        invoice_id=invoice_id,
        action=action,
        reason=reason,
        data=data,
        prev_hash=prev_hash,
    )
    row = AuditEvent(
        seq=seq,
        at=at,
        actor=actor,
        entity_id=entity_id,
        invoice_id=invoice_id,
        action=action,
        reason=reason,
        data_json=data_json,
        prev_hash=prev_hash,
        hash=event_hash(**fields),
    )
    session.add(row)
    # Flush now so the next append in this transaction sees this event as the chain tail.
    session.flush()
    return row


def verify_chain(conn: Connection | Session) -> VerifyResult:
    """Recompute every link of the chain. Reads with Core so no ORM cache is involved."""
    rows = conn.execute(select(AuditEvent.__table__).order_by(AuditEvent.__table__.c.seq))
    breaks: list[ChainBreak] = []
    expected_prev = GENESIS_HASH
    expected_seq = 1
    checked = 0
    for row in rows.mappings():
        checked += 1
        seq = row["seq"]
        if seq != expected_seq:
            breaks.append(ChainBreak(seq, f"sequence gap: expected {expected_seq}"))
        if row["prev_hash"] != expected_prev:
            breaks.append(ChainBreak(seq, "prev_hash does not match the previous event's hash"))
        try:
            data = json.loads(row["data_json"])
            recomputed = event_hash(
                seq=seq,
                at=row["at"],
                actor=row["actor"],
                entity_id=row["entity_id"],
                invoice_id=row["invoice_id"],
                action=row["action"],
                reason=row["reason"],
                data=data,
                prev_hash=row["prev_hash"],
            )
        except (ValueError, TypeError) as exc:
            breaks.append(ChainBreak(seq, f"unreadable event data: {exc}"))
        else:
            if recomputed != row["hash"]:
                breaks.append(ChainBreak(seq, "hash does not match the event's contents"))
        expected_prev = row["hash"]
        expected_seq = seq + 1
    return VerifyResult(checked=checked, breaks=tuple(breaks))


# --- append-only guard at the ORM level (the migration adds DB triggers as well) ---------


@event.listens_for(Session, "before_flush")
def _block_audit_mutation(session: Session, _ctx: UOWTransaction, _instances: Any) -> None:
    for obj in (*session.dirty, *session.deleted):
        if isinstance(obj, AuditEvent):
            raise AppendOnlyViolation("audit events cannot be updated or deleted")


@event.listens_for(Session, "do_orm_execute")
def _block_audit_bulk_mutation(state: Any) -> None:
    if not (state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    if getattr(table, "name", None) == AuditEvent.__tablename__ or any(
        m.class_ is AuditEvent for m in state.all_mappers
    ):
        raise AppendOnlyViolation("audit events cannot be updated or deleted")
