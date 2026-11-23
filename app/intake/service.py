"""The intake service: one file in, one invoice in `received` out (FR-10, FR-13).

For each file: check it (`files.check_file`), decide its entity, store it under a generated
name, then in one database transaction create the invoice (through `transitions.create_invoice`,
which writes `invoice.received`), raise the exact-duplicate `block` when the same SHA-256 is
already on file for that entity, and queue its extraction job. Invoice, flag, audit events
and job commit together or not at all; if the transaction fails the stored file is removed.

Web upload (S7) calls this with the signed-in clerk as the actor. Email and folder intake (S14)
will call it too.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app import jobs
from app.accounts.models import Entity, GstRegistration
from app.domain.actor import Actor, Role
from app.domain.flags import FlagSeverity, raise_flag
from app.domain.models import Invoice
from app.domain.transitions import create_invoice
from app.duplicates import EXACT_FILE
from app.intake.files import CheckedFile, Rejected, check_file
from app.intake.store import FileStore
from app.scoping import EntityScope

log = logging.getLogger("invoice_flow.intake")

SOURCE_UPLOAD = "upload"
MAX_NAME_LENGTH = 255
COMMIT_ATTEMPTS = 3  # audit-chain `seq` collisions with a concurrent writer are retried

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class FileResult:
    """What the clerk sees for one file: accepted (with the invoice) or rejected (with why)."""

    name: str
    accepted: bool
    reason: str | None = None
    invoice_id: int | None = None
    entity_name: str | None = None
    duplicate_of: int | None = None


@dataclass(frozen=True)
class Limits:
    max_bytes: int
    max_pages: int


def display_name(raw: str | None) -> str:
    """The uploader's file name, for display only: no path, no control characters."""
    name = re.split(r"[\\/]", raw or "")[-1]
    name = " ".join(_CONTROL.sub("", name).split())[:MAX_NAME_LENGTH]
    return name or "Unnamed file"


def clerk_scope(actor: Actor) -> EntityScope:
    return EntityScope(actor)


def resolve_entity(
    session: Session, actor: Actor, entity_id: int | None, checked: CheckedFile
) -> int:
    """The entity the invoice belongs to. `entity_id` None means "auto-assign".

    Auto-assignment: a clerk of one entity gets that entity. Otherwise the PDF's text layer must
    show a GSTIN registered to exactly one of the entities where the actor is a clerk (the buyer
    block). The text only selects among the actor's own entities; it can never widen access.
    """
    clerk_entities = clerk_scope(actor).entity_ids(Role.CLERK)
    if entity_id is not None:
        if entity_id not in clerk_entities:
            raise Rejected("You cannot upload invoices for that entity.")
        return entity_id
    if len(clerk_entities) == 1:
        return next(iter(clerk_entities))
    matches = set()
    if checked.gstins and clerk_entities:
        matches = set(
            session.scalars(
                select(GstRegistration.entity_id).where(
                    GstRegistration.gstin.in_(sorted(checked.gstins)),
                    GstRegistration.entity_id.in_(sorted(clerk_entities)),
                )
            )
        )
    if len(matches) == 1:
        return matches.pop()
    raise Rejected(
        "Could not tell which entity this invoice is for. Choose the entity and upload it again."
    )


def _first_with_hash(session: Session, actor: Actor, entity_id: int, sha256: str) -> int | None:
    """The oldest invoice of `entity_id` with this file hash, through the entity scope."""
    stmt = select(Invoice.id).where(Invoice.entity_id == entity_id, Invoice.file_hash == sha256)
    stmt = clerk_scope(actor).where(stmt, Invoice, Role.CLERK)
    return session.scalars(stmt.order_by(Invoice.id).limit(1)).first()


def _record(
    session: Session,
    actor: Actor,
    entity_id: int,
    checked: CheckedFile,
    name: str,
    stored_name: str,
) -> tuple[Invoice, int | None]:
    duplicate_of = _first_with_hash(session, actor, entity_id, checked.sha256)
    invoice = create_invoice(
        session,
        actor,
        entity_id=entity_id,
        source=SOURCE_UPLOAD,
        data={
            "file_sha256": checked.sha256,
            "file_type": checked.file_type.value,
            "file_size": checked.size,
            "page_count": checked.page_count,
        },
        file_hash=checked.sha256,
        file_name=name,
        stored_name=stored_name,
        file_type=checked.file_type.value,
        file_size=checked.size,
        page_count=checked.page_count,
        uploaded_by=actor.user_id,
    )
    if duplicate_of is not None:
        raise_flag(
            session,
            actor,
            invoice_id=invoice.id,
            entity_id=entity_id,
            rule_id=EXACT_FILE,
            severity=FlagSeverity.BLOCK,
            message=(
                f"Exact duplicate: the same file is already on file as invoice {duplicate_of}. "
                "Paying it twice would pay the supplier twice. Discard this copy, or override "
                "with a reason if it really is a separate invoice."
            ),
            related_invoice_id=duplicate_of,
        )
    jobs.enqueue(session, jobs.EXTRACT, invoice_id=invoice.id)
    return invoice, duplicate_of


def intake_file(
    sessions: sessionmaker[Session],
    store: FileStore,
    actor: Actor,
    *,
    file_name: str | None,
    stream: BinaryIO,
    entity_id: int | None,
    limits: Limits,
) -> FileResult:
    """Take in one file. Never raises for a bad file: the result says why it was rejected."""
    name = display_name(file_name)
    try:
        checked = check_file(stream, max_bytes=limits.max_bytes, max_pages=limits.max_pages)
        with sessions() as session:
            target = resolve_entity(session, actor, entity_id, checked)
    except Rejected as exc:
        return FileResult(name=name, accepted=False, reason=exc.reason)

    stored_name = store.save(stream, checked.file_type)
    for attempt in range(1, COMMIT_ATTEMPTS + 1):
        with sessions() as session:
            try:
                invoice, duplicate_of = _record(session, actor, target, checked, name, stored_name)
                session.commit()
                entity_name = session.get(Entity, target).legal_name
            except (IntegrityError, OperationalError) as exc:
                session.rollback()
                if attempt < COMMIT_ATTEMPTS:
                    time.sleep(0.05 * attempt)
                    continue
                store.delete(stored_name)
                log.warning("intake failed to save after retries: %s", type(exc).__name__)
                return FileResult(
                    name=name,
                    accepted=False,
                    reason="The file could not be saved just now. Upload it again.",
                )
            except Exception as exc:
                # Any other failure loses only this file: it is rolled back and removed, and
                # the clerk sees a reason for it beside the results of the other files.
                session.rollback()
                store.delete(stored_name)
                log.error("intake failed to save a file: %s", type(exc).__name__)
                return FileResult(
                    name=name,
                    accepted=False,
                    reason="The file could not be saved. Upload it again; if it fails again, "
                    "tell your admin.",
                )
            except BaseException:
                session.rollback()
                store.delete(stored_name)
                raise
        log.info(
            "invoice %s received: entity %s, %s, %s bytes, %s pages%s",
            invoice.id,
            target,
            checked.file_type.value,
            checked.size,
            checked.page_count,
            f", exact duplicate of {duplicate_of}" if duplicate_of else "",
        )
        return FileResult(
            name=name,
            accepted=True,
            invoice_id=invoice.id,
            entity_name=entity_name,
            duplicate_of=duplicate_of,
        )
    raise AssertionError("unreachable")  # pragma: no cover
