"""The `jobs` table: a DB-backed work queue with claim-by-update (spec §5.1, NFR-4).

S1 created the table; S7 adds the queue operations below and the worker (`app/worker.py`).

A job is claimed by one `UPDATE ... RETURNING` that sets `status='running'`, bumps
`attempts` and sets `locked_until` (now + the lock time, 10 minutes by default). SQLite runs
one writer at a time, so two workers can never claim the same row. A worker that crashes
leaves its job `running` with a lock in the past; the next claim after the lock expires
takes it again, so a crash loses nothing. `attempts` doubles as a fencing token: finishing
or failing a job checks it, so a worker whose job was re-claimed cannot overwrite the
newer run's result, and its own work (done in the same transaction) is rolled back.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s.value}'" for s in JobStatus) + ")",
            name="ck_jobs_status",
        ),
        Index("ix_jobs_status_locked_until", "status", "locked_until"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.QUEUED.value)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


# --- queue operations ----------------------------------------------------------------------

EXTRACT = "extract"  # run by slice S9's extraction handler

DEFAULT_LOCK = timedelta(minutes=10)  # NFR-4
DEFAULT_MAX_ATTEMPTS = 3
RETRY_BACKOFF = timedelta(seconds=30)  # times the attempt number
MAX_ERROR_LENGTH = 500


@dataclass(frozen=True)
class ClaimedJob:
    """A job one worker holds. `attempts` is the fencing token for finish and fail."""

    id: int
    kind: str
    invoice_id: int | None
    attempts: int


def enqueue(session: Session, kind: str, *, invoice_id: int | None = None) -> Job:
    """Add a queued job in the caller's transaction (no commit), so it commits with its cause."""
    job = Job(kind=kind, invoice_id=invoice_id, status=JobStatus.QUEUED.value, attempts=0)
    session.add(job)
    session.flush()
    return job


def _claimable(now: datetime, max_attempts: int):
    return and_(
        Job.attempts < max_attempts,
        or_(
            and_(
                Job.status == JobStatus.QUEUED.value,
                or_(Job.locked_until.is_(None), Job.locked_until <= now),
            ),
            # Stuck: claimed, but the lock ran out without a finish or a failure (a crash).
            and_(Job.status == JobStatus.RUNNING.value, Job.locked_until < now),
        ),
    )


def claim(
    session: Session,
    kinds: Iterable[str],
    *,
    now: datetime | None = None,
    lock: timedelta = DEFAULT_LOCK,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ClaimedJob | None:
    """Claim the oldest runnable job of one of `kinds` and commit the claim, or return None."""
    kinds = sorted(set(kinds))
    if not kinds:
        return None
    now = now or _utcnow()
    oldest = (
        select(Job.id)
        .where(Job.kind.in_(kinds), _claimable(now, max_attempts))
        .order_by(Job.id)
        .limit(1)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == oldest, _claimable(now, max_attempts))
        .values(
            status=JobStatus.RUNNING.value,
            attempts=Job.attempts + 1,
            locked_until=now + lock,
            updated_at=now,
        )
        .returning(Job.id, Job.kind, Job.invoice_id, Job.attempts)
        .execution_options(synchronize_session=False)
    )
    row = session.execute(stmt).first()
    session.commit()
    if row is None:
        return None
    return ClaimedJob(id=row.id, kind=row.kind, invoice_id=row.invoice_id, attempts=row.attempts)


def _fenced(job: ClaimedJob):
    return and_(
        Job.id == job.id,
        Job.status == JobStatus.RUNNING.value,
        Job.attempts == job.attempts,
    )


def finish(session: Session, job: ClaimedJob, *, now: datetime | None = None) -> bool:
    """Mark the job done in the caller's transaction (no commit).

    Returns False if another worker has re-claimed it since; the caller must then roll back.
    """
    now = now or _utcnow()
    result = session.execute(
        update(Job)
        .where(_fenced(job))
        .values(status=JobStatus.DONE.value, locked_until=None, last_error=None, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def fail(
    session: Session,
    job: ClaimedJob,
    error: str,
    *,
    now: datetime | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> bool:
    """Requeue the job with a back-off, or mark it failed after `max_attempts` (no commit)."""
    now = now or _utcnow()
    final = job.attempts >= max_attempts
    values = {
        "status": JobStatus.FAILED.value if final else JobStatus.QUEUED.value,
        "locked_until": None if final else now + RETRY_BACKOFF * job.attempts,
        "last_error": error[:MAX_ERROR_LENGTH],
        "updated_at": now,
    }
    result = session.execute(
        update(Job)
        .where(_fenced(job))
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


def reap(
    session: Session, *, now: datetime | None = None, max_attempts: int = DEFAULT_MAX_ATTEMPTS
) -> int:
    """Fail stuck jobs that have used every attempt (each one crashed or timed out). Commits."""
    now = now or _utcnow()
    result = session.execute(
        update(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.locked_until < now,
            Job.attempts >= max_attempts,
        )
        .values(
            status=JobStatus.FAILED.value,
            locked_until=None,
            last_error="timed out: the worker stopped or the job ran past its lock",
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return result.rowcount


def counts(session: Session) -> dict[str, int]:
    """Number of jobs per status (for `/health`)."""
    rows = session.execute(select(Job.status, func.count()).group_by(Job.status))
    found = {status: n for status, n in rows}
    return {s.value: found.get(s.value, 0) for s in JobStatus}
