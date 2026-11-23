"""The in-process job worker (spec §5.1, NFR-1, NFR-4).

`Worker` runs `threads` daemon threads (4 by default, `INVOICE_FLOW_WORKER_THREADS`). Each
loop claims one job from the `jobs` table (`app.jobs.claim`), runs the handler registered for
its kind, and marks it done in the same transaction as the handler's own writes. A handler
that raises is rolled back and its job is requeued with a back-off, or failed after the last
attempt. A worker that dies mid-job leaves the job `running`; once its lock (10 minutes)
runs out, any worker claims it again.

Handlers are `handler(session, job) -> None`. They must not commit: the worker commits their
work together with the job's completion, or rolls both back. They may run more than once for
the same job (after a crash), so they must be safe to repeat. SQLite has one writer at a time,
so a handler does its slow part (the Claude API call) before its first write, keeping its write
transaction short (plan.md risk "SQLite with worker threads"). S7 registers no handler; slice S9
registers `extract`, so until then upload jobs stay queued and invoices stay `received`.

Logs carry job and invoice IDs and the exception type only, never invoice content
(data-protection §1).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import jobs
from app.jobs import ClaimedJob

log = logging.getLogger("invoice_flow.worker")

Handler = Callable[[Session, ClaimedJob], None]


class Worker:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        handlers: Mapping[str, Handler],
        *,
        threads: int = 4,
        poll_seconds: float = 1.0,
        lock: timedelta = jobs.DEFAULT_LOCK,
        max_attempts: int = jobs.DEFAULT_MAX_ATTEMPTS,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.sessions = sessions
        # The live mapping, so a handler registered after start (by a later slice's setup)
        # is picked up on the next claim.
        self.handlers = handlers
        self.threads = threads
        self.poll_seconds = poll_seconds
        self.lock = lock
        self.max_attempts = max_attempts
        self.clock = clock  # tests move time forward to expire locks
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # --- one unit of work (also what tests call) -------------------------------------------

    def run_once(self) -> bool:
        """Claim and run one job. Returns False when there was nothing to do."""
        with self.sessions() as session:
            jobs.reap(session, now=self.clock(), max_attempts=self.max_attempts)
            job = jobs.claim(
                session,
                self.handlers.keys(),
                now=self.clock(),
                lock=self.lock,
                max_attempts=self.max_attempts,
            )
        if job is None:
            return False
        handler = self.handlers.get(job.kind)
        with self.sessions() as session:
            try:
                if handler is None:  # unregistered between claim and run
                    raise LookupError(f"no handler for job kind {job.kind!r}")
                handler(session, job)
                if not jobs.finish(session, job, now=self.clock()):
                    session.rollback()
                    log.warning("job %s was re-claimed by another worker; result dropped", job.id)
                    return True
                session.commit()
            except Exception as exc:
                session.rollback()
                log.warning(
                    "job %s (%s, invoice %s) attempt %s failed: %s",
                    job.id,
                    job.kind,
                    job.invoice_id,
                    job.attempts,
                    type(exc).__name__,
                )
                jobs.fail(
                    session,
                    job,
                    _error_text(exc),
                    now=self.clock(),
                    max_attempts=self.max_attempts,
                )
                session.commit()
        return True

    # --- threads ------------------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = self.run_once()
            except OperationalError as exc:  # e.g. "database is locked" past busy_timeout
                log.warning("worker database error: %s", type(exc).__name__)
                busy = False
            except Exception:
                log.exception("worker loop error")
                busy = False
            if not busy:
                self._stop.wait(self.poll_seconds)

    def start(self) -> None:
        if self.threads <= 0 or self._threads:
            return
        self._stop.clear()
        for n in range(self.threads):
            thread = threading.Thread(
                target=self._loop, name=f"invoice-flow-worker-{n}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        log.info("worker started with %s threads", self.threads)

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout)
        self._threads = []

    @property
    def alive_threads(self) -> int:
        return sum(1 for t in self._threads if t.is_alive())

    @property
    def status(self) -> str:
        """`running` (every thread alive), `degraded` (some), `stopped`, or `disabled`."""
        if self.threads <= 0:
            return "disabled"
        alive = self.alive_threads
        if alive == 0:
            return "stopped"
        return "running" if alive == self.threads else "degraded"


def _error_text(exc: Exception) -> str:
    """What `jobs.last_error` keeps: the exception type and message, for operators.

    Handlers must raise messages without restricted data (data-protection §1); the text is
    stored in the database only, never logged.
    """
    return f"{type(exc).__name__}: {exc}"
