"""Jobs queue and in-process worker (S7; spec §5.1, NFR-1, NFR-4).

Claim-by-update, retry of jobs stuck for over 10 minutes, fencing of stale workers, back-off and
final failure, and "a crash loses nothing": a worker that dies mid-job leaves its job to be
claimed again, and none of its half-done work is committed.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import jobs
from app.accounts.models import as_utc
from app.audit.chain import append_event
from app.audit.models import AuditEvent
from app.config import Settings
from app.db import make_engine, make_sessionmaker
from app.jobs import Job, JobStatus
from app.main import create_app
from app.worker import Worker

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
TEN_MIN = timedelta(minutes=10)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


def add_jobs(sessions, n: int, kind: str = "test") -> list[int]:
    with sessions() as s:
        ids = [jobs.enqueue(s, kind).id for _ in range(n)]
        s.commit()
    return ids


def job(sessions, job_id: int) -> Job:
    with sessions() as s:
        return s.get(Job, job_id)


def audit_count(sessions) -> int:
    with sessions() as s:
        return s.scalar(select(func.count()).select_from(AuditEvent))


def mark(session, claimed) -> None:
    """A handler's own write: an audit event, so tests can see whether it was committed."""
    append_event(session, actor="system:test", action="test.ran", data={"job": claimed.id})


# --- claim-by-update -------------------------------------------------------------------------


def test_claim_takes_the_oldest_job_and_locks_it_for_10_minutes(sessions):
    first, _second = add_jobs(sessions, 2)
    with sessions() as s:
        claimed = jobs.claim(s, ["test"], now=T0)
    assert claimed.id == first and claimed.attempts == 1
    row = job(sessions, first)
    assert row.status == JobStatus.RUNNING
    assert as_utc(row.locked_until) == T0 + TEN_MIN
    assert jobs.DEFAULT_LOCK == TEN_MIN


def test_a_claimed_job_is_not_claimed_again_while_locked(sessions):
    add_jobs(sessions, 1)
    with sessions() as s:
        assert jobs.claim(s, ["test"], now=T0) is not None
        assert jobs.claim(s, ["test"], now=T0 + timedelta(minutes=9, seconds=59)) is None


def test_claim_only_takes_kinds_it_has_handlers_for(sessions):
    add_jobs(sessions, 1, kind=jobs.EXTRACT)
    with sessions() as s:
        assert jobs.claim(s, ["test"], now=T0) is None
        assert jobs.claim(s, [], now=T0) is None
        assert jobs.claim(s, [jobs.EXTRACT], now=T0) is not None


def test_concurrent_workers_claim_each_job_exactly_once(sessions):
    ids = add_jobs(sessions, 40)
    claimed: list[int] = []
    lock = threading.Lock()

    def drain():
        while True:
            with sessions() as s:
                got = jobs.claim(s, ["test"])
            if got is None:
                return
            with lock:
                claimed.append(got.id)

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(claimed) == ids  # every job once, none twice


# --- stuck jobs and crashes (NFR-4) -----------------------------------------------------------


def test_a_job_stuck_for_over_10_minutes_is_claimed_again(sessions):
    (job_id,) = add_jobs(sessions, 1)
    with sessions() as s:
        jobs.claim(s, ["test"], now=T0)  # this worker then dies: no finish, no fail
        assert jobs.claim(s, ["test"], now=T0 + TEN_MIN) is None  # not yet "over" 10 minutes
        again = jobs.claim(s, ["test"], now=T0 + TEN_MIN + timedelta(seconds=1))
    assert again.id == job_id and again.attempts == 2


def test_a_crash_mid_job_loses_nothing(engine, settings):
    """A worker dies inside a handler (the process is killed). Its half-done work is rolled
    back and the job stays in the table. After a restart (new engine, new worker), the job is
    retried once its 10-minute lock has run out, and completes."""
    sessions = make_sessionmaker(engine)
    (job_id,) = add_jobs(sessions, 1)
    events_before = audit_count(sessions)
    clock = Clock()

    class ProcessKilled(BaseException):
        """Stands in for SIGKILL: not an Exception, so nothing in the worker handles it."""

    def dies_halfway(session, claimed):
        mark(session, claimed)  # written in the transaction, never committed
        raise ProcessKilled

    crashing = Worker(sessions, {"test": dies_halfway}, threads=0, clock=clock)
    with pytest.raises(ProcessKilled):
        crashing.run_once()
    row = job(sessions, job_id)
    assert row.status == JobStatus.RUNNING and row.attempts == 1
    assert audit_count(sessions) == events_before  # the half-done work did not commit

    # Restart: a fresh engine and worker on the same database file.
    engine.dispose()
    restarted = make_sessionmaker(make_engine(settings.database_url))
    ran: list[int] = []

    def works(session, claimed):
        mark(session, claimed)
        ran.append(claimed.id)

    worker = Worker(restarted, {"test": works}, threads=0, clock=clock)
    clock.advance(timedelta(minutes=5))
    assert worker.run_once() is False  # still locked by the dead worker
    clock.advance(timedelta(minutes=5, seconds=1))
    assert worker.run_once() is True
    assert ran == [job_id]
    row = job(restarted, job_id)
    assert row.status == JobStatus.DONE and row.attempts == 2 and row.locked_until is None
    assert audit_count(restarted) == events_before + 1


def test_a_stale_worker_cannot_finish_a_job_that_was_claimed_again(sessions):
    """Worker A runs past its lock; B claims and finishes the job. A's result is dropped."""
    (job_id,) = add_jobs(sessions, 1)
    events_before = audit_count(sessions)
    with sessions() as s:
        a = jobs.claim(s, ["test"], now=T0)
        b = jobs.claim(s, ["test"], now=T0 + TEN_MIN + timedelta(seconds=1))
    with sessions() as s:
        mark(s, b)
        assert jobs.finish(s, b) is True
        s.commit()
    with sessions() as s:
        mark(s, a)
        assert jobs.finish(s, a) is False  # fenced out by `attempts`
        s.rollback()
        assert jobs.fail(s, a, "late") is False
        s.rollback()
    assert job(sessions, job_id).status == JobStatus.DONE
    assert audit_count(sessions) == events_before + 1  # only B's work


def test_the_worker_drops_its_result_when_its_job_was_claimed_again(sessions):
    (job_id,) = add_jobs(sessions, 1)
    events_before = audit_count(sessions)
    clock = Clock()

    def slow(session, claimed):
        # While this handler is busy (say, waiting on the API) past its lock, another worker
        # takes the job and finishes it. Then this handler writes its result.
        with sessions() as other:
            again = jobs.claim(other, ["test"], now=clock() + TEN_MIN + timedelta(seconds=1))
            jobs.finish(other, again)
            other.commit()
        mark(session, claimed)

    Worker(sessions, {"test": slow}, threads=0, clock=clock).run_once()
    row = job(sessions, job_id)
    assert row.status == JobStatus.DONE and row.attempts == 2
    assert audit_count(sessions) == events_before  # the stale run's write was rolled back


# --- handler failures ---------------------------------------------------------------------------


def test_a_failing_handler_is_rolled_back_and_requeued_with_back_off(sessions):
    (job_id,) = add_jobs(sessions, 1)
    events_before = audit_count(sessions)
    clock = Clock()

    def broken(session, claimed):
        mark(session, claimed)
        raise ValueError("schema-invalid output")

    worker = Worker(sessions, {"test": broken}, threads=0, clock=clock, max_attempts=3)
    assert worker.run_once() is True
    row = job(sessions, job_id)
    assert row.status == JobStatus.QUEUED and row.attempts == 1
    assert row.last_error == "ValueError: schema-invalid output"
    assert as_utc(row.locked_until) == T0 + jobs.RETRY_BACKOFF
    assert audit_count(sessions) == events_before
    assert worker.run_once() is False  # backing off
    clock.advance(jobs.RETRY_BACKOFF)
    assert worker.run_once() is True
    assert job(sessions, job_id).attempts == 2


def test_a_job_fails_for_good_after_the_last_attempt(sessions):
    (job_id,) = add_jobs(sessions, 1)
    clock = Clock()

    def broken(session, claimed):
        raise RuntimeError("API unavailable")

    worker = Worker(sessions, {"test": broken}, threads=0, clock=clock, max_attempts=2)
    worker.run_once()
    clock.advance(timedelta(hours=1))
    worker.run_once()
    row = job(sessions, job_id)
    assert row.status == JobStatus.FAILED and row.attempts == 2 and row.locked_until is None
    clock.advance(timedelta(hours=1))
    assert worker.run_once() is False


def test_a_job_that_keeps_crashing_its_worker_is_failed_after_the_last_attempt(sessions):
    (job_id,) = add_jobs(sessions, 1)
    with sessions() as s:
        for n in range(jobs.DEFAULT_MAX_ATTEMPTS):
            assert jobs.claim(s, ["test"], now=T0 + n * 2 * TEN_MIN) is not None
        later = T0 + 10 * TEN_MIN
        assert jobs.claim(s, ["test"], now=later) is None
        assert jobs.reap(s, now=later) == 1
    row = job(sessions, job_id)
    assert row.status == JobStatus.FAILED and "timed out" in row.last_error


def test_counts_by_status(sessions):
    add_jobs(sessions, 2)
    with sessions() as s:
        jobs.claim(s, ["test"], now=T0)
        assert jobs.counts(s) == {"queued": 1, "running": 1, "done": 0, "failed": 0}


# --- threads, configuration and /health --------------------------------------------------------


def wait_for(predicate, timeout: float = 5.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_worker_threads_drain_the_queue(sessions):
    ids = add_jobs(sessions, 12)
    seen: list[int] = []
    lock = threading.Lock()

    def handler(session, claimed):
        with lock:
            seen.append(claimed.id)

    worker = Worker(sessions, {"test": handler}, threads=4, poll_seconds=0.02)
    worker.start()
    try:
        assert worker.status == "running" and worker.alive_threads == 4
        assert wait_for(lambda: len(seen) == len(ids))
    finally:
        worker.stop()
    assert sorted(seen) == ids
    assert worker.status == "stopped"
    with sessions() as s:
        assert jobs.counts(s)["done"] == 12


def test_worker_status_values():
    assert Worker(None, {}, threads=0).status == "disabled"
    assert Worker(None, {}, threads=2).status == "stopped"


def test_the_app_runs_4_worker_threads_by_default_and_health_reports_them(app):
    assert Settings().worker_threads == 4
    with TestClient(app) as client:
        worker = app.state.worker
        assert worker.threads == 4 and worker.alive_threads == 4
        assert client.get("/health").json()["worker"] == "running"
    assert worker.status == "stopped"  # stopped with the app


def test_worker_thread_count_is_configurable(tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_FLOW_WORKER_THREADS", "2")
    app = create_app(Settings(data_dir=tmp_path))
    with TestClient(app) as client:
        assert app.state.worker.alive_threads == 2
        assert client.get("/health").json() == {
            "status": "ok",
            "database": "ok",
            "worker": "running",
        }


def test_health_is_degraded_when_the_worker_is_not_running(app):
    # Without the lifespan (no `with`), the worker never started.
    body = TestClient(app).get("/health").json()
    assert body == {"status": "degraded", "database": "ok", "worker": "stopped"}


def test_health_is_ok_with_the_worker_switched_off(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, worker_threads=0))
    with TestClient(app) as client:
        assert client.get("/health").json() == {
            "status": "ok",
            "database": "ok",
            "worker": "disabled",
        }
