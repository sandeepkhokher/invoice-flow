"""Append-only, hash-chained audit trail (FR-60, approval-and-audit §5)."""

from app.audit.chain import (
    GENESIS_HASH,
    AppendOnlyViolation,
    ChainBreak,
    append_event,
    canonical_json,
    event_hash,
    verify_chain,
)
from app.audit.models import AuditEvent

__all__ = [
    "GENESIS_HASH",
    "AppendOnlyViolation",
    "AuditEvent",
    "ChainBreak",
    "append_event",
    "canonical_json",
    "event_hash",
    "verify_chain",
]
