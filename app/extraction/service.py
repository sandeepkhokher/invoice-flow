"""The `extract` job: a `received` invoice in, extracted fields, flags and a new state out.

(spec FR-20 to FR-23, FR-25, FR-30, FR-31; data-protection §4-5; concern C8)

1. In its own short transaction: `received -> extracting` (the list shows it at once).
2. Outside any transaction: read the stored file and call the extractor (the slow part; SQLite
   has one writer, so no write transaction is held during the API call).
3. In the job's transaction (committed by the worker with the job's completion):
   - failure: `extracting -> extraction_failed` with the reason (FR-22);
   - success: store every field with its merged confidence (FR-21), copy the header values onto
     the invoice, validate with `IndiaGST` (S2) and the duplicate library (S3) and raise their
     flags, write `invoice.extracted`, then route (FR-23): straight to `ready_for_approval`,
     marked auto-reviewed, when the entity allows the touch-free path, every field is `high`
     and no open `block` or `warn` flag exists; otherwise to `needs_review`.

Model output only fills fields. It never chooses the state, the entity, a flag or its severity:
those come from the deterministic code below (data-protection §4). Logs carry IDs, counts and
outcomes, never field values.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.accounts.models import Entity, GstRegistration
from app.audit.chain import append_event
from app.domain.actor import Actor, Role
from app.domain.flags import Flag, FlagSeverity, FlagStatus, raise_flag
from app.domain.models import Invoice
from app.domain.money import Money
from app.domain.states import InvoiceState as S
from app.domain.transitions import (
    extraction_failed,
    extraction_succeeded,
    extraction_touch_free,
    start_extraction,
)
from app.duplicates import (
    EXACT_NUMBER,
    NEAR_AMOUNT_DATE,
    NEAR_NUMBER,
    InvoiceRecord,
    financial_year_label,
    find_duplicates,
    normalise_invoice_number,
)
from app.extraction.checks import (
    ScoredField,
    normalise_gstin,
    parse_amount,
    parse_date,
    pdf_text_layer,
    score,
    to_tax_invoice,
)
from app.extraction.extractor import Document, ExtractionFailed, ExtractionResult, Extractor
from app.extraction.models import InvoiceField
from app.extraction.schema import RESTRICTED_FIELDS
from app.intake.files import FileType
from app.intake.store import FileStore
from app.jobs import ClaimedJob
from app.scoping import EntityScope
from app.tax import IndiaGST
from app.tax.flags import Flag as TaxFlag

log = logging.getLogger("invoice_flow.extraction")

SYSTEM = Actor.system("extraction")
START_ATTEMPTS = 3
DUPLICATE_MESSAGES = {
    EXACT_NUMBER: (
        "Duplicate: invoice {other} has the same supplier GSTIN, invoice number and financial "
        "year. Paying both would pay the supplier twice. Discard this copy, or override with a "
        "reason if it really is a separate invoice."
    ),
    NEAR_NUMBER: (
        "Possible duplicate of invoice {other}: same supplier, an invoice number one character "
        "apart, dated within 30 days. Compare the two before approving."
    ),
    NEAR_AMOUNT_DATE: (
        "Possible duplicate of invoice {other}: same supplier, a total within 1%, dated within "
        "30 days. Compare the two before approving."
    ),
}


def mask(value: str | None) -> str | None:
    """Restricted values keep only their last 4 characters (data-protection §1)."""
    if value is None:
        return None
    return "X" * max(len(value) - 4, 0) + value[-4:] if len(value) > 4 else "X" * len(value)


def _scope(entity_id: int) -> EntityScope:
    """The worker's view for duplicate lookups: the invoice's own entity only (C2 is S11's)."""
    return EntityScope(
        Actor(user_id=None, system_name="extraction", roles_by_entity={entity_id: {Role.CLERK}})
    )


@dataclass(frozen=True)
class _Source:
    id: int
    stored_name: str | None
    file_type: str | None
    file_hash: str | None


@dataclass(frozen=True)
class Outcome:
    route: str  # "touch_free", "needs_review", "failed" or "skipped"
    fields: int = 0
    below_high: int = 0
    blocks: int = 0
    warnings: int = 0


class ExtractionService:
    """Holds the extractor (tests and settings swap it) and runs `extract` jobs."""

    def __init__(
        self,
        sessions: sessionmaker[Session],
        store: FileStore,
        extractor: Extractor,
        *,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self.sessions = sessions
        self.store = store
        self.extractor = extractor
        self.today = today

    # --- the job handler -----------------------------------------------------------------

    def handle(self, session: Session, job: ClaimedJob) -> None:
        if job.invoice_id is None or not self._start(job.invoice_id):
            return
        with self.sessions() as s:  # a short read; nothing is held open during the API call
            source = s.get(Invoice, job.invoice_id)
            stored = _Source(source.id, source.stored_name, source.file_type, source.file_hash)
        text_layer, result, reason = self._extract(stored)
        invoice = session.get(Invoice, job.invoice_id)
        if invoice.state is not S.EXTRACTING:  # changed meanwhile; nothing for us to do
            return
        started = time.monotonic()
        if result is None:
            extraction_failed(
                session, invoice, SYSTEM, reason=reason, data={"extractor": self.extractor.name}
            )
            outcome = Outcome(route="failed")
        else:
            outcome = self.apply(session, invoice, result, text_layer)
        log.info(
            "extraction done invoice=%s extractor=%s route=%s fields=%d below_high=%d "
            "flags_block=%d flags_warn=%d apply_ms=%d",
            invoice.id,
            self.extractor.name,
            outcome.route,
            outcome.fields,
            outcome.below_high,
            outcome.blocks,
            outcome.warnings,
            round((time.monotonic() - started) * 1000),
        )

    def _start(self, invoice_id: int) -> bool:
        """`received -> extracting`, committed now. True if the invoice is now extracting."""
        for attempt in range(1, START_ATTEMPTS + 1):
            with self.sessions() as s:
                invoice = s.get(Invoice, invoice_id)
                if invoice is None or invoice.state not in (S.RECEIVED, S.EXTRACTING):
                    return False
                if invoice.state is S.EXTRACTING:  # a re-run after a crash or a clerk's retry
                    return True
                try:
                    start_extraction(s, invoice, SYSTEM)
                    s.commit()
                    return True
                except (IntegrityError, OperationalError):  # audit `seq` race with a writer
                    s.rollback()
                    if attempt == START_ATTEMPTS:
                        raise
                    time.sleep(0.05 * attempt)
        return False  # pragma: no cover

    def _extract(self, source: _Source) -> tuple[str | None, ExtractionResult | None, str | None]:
        try:
            data = self.store.path(source.stored_name or "").read_bytes()
            file_type = FileType(source.file_type)
        except (OSError, ValueError):
            return None, None, "The stored file is missing. Upload the invoice again."
        doc = Document(
            invoice_id=source.id, data=data, file_type=file_type, sha256=source.file_hash or ""
        )
        text_layer = pdf_text_layer(data) if file_type is FileType.PDF else None
        try:
            return text_layer, self.extractor.extract(doc), None
        except ExtractionFailed as exc:
            return text_layer, None, exc.reason
        except Exception as exc:  # a bug or an unreadable file: fail visibly, never stay stuck
            log.error(
                "extraction error invoice=%s extractor=%s error=%s",
                source.id,
                self.extractor.name,
                type(exc).__name__,
            )
            return text_layer, None, "Extraction failed unexpectedly. Retry the extraction."

    # --- applying a result ---------------------------------------------------------------

    def apply(
        self,
        session: Session,
        invoice: Invoice,
        result: ExtractionResult,
        text_layer: str | None,
    ) -> Outcome:
        entity = session.get(Entity, invoice.entity_id)
        gstins = session.scalars(
            select(GstRegistration.gstin).where(GstRegistration.entity_id == invoice.entity_id)
        ).all()
        tax_invoice = to_tax_invoice(result.invoice)
        tax_flags = IndiaGST(gstins).validate(tax_invoice)
        scored = score(
            result.invoice, tax_flags=tax_flags, text_layer=text_layer, today=self.today()
        )

        self._store_fields(session, invoice, scored)
        self._copy_header(invoice, result)
        self._raise_tax_flags(session, invoice, tax_flags)
        self._raise_duplicate_flags(session, invoice)
        session.flush()

        below_high = sum(1 for f in scored if f.confidence != "high")
        open_counts = dict(
            session.execute(
                select(Flag.severity, func.count())
                .where(Flag.invoice_id == invoice.id, Flag.status == FlagStatus.OPEN.value)
                .group_by(Flag.severity)
            ).all()
        )
        blocks = open_counts.get(FlagSeverity.BLOCK.value, 0)
        warnings = open_counts.get(FlagSeverity.WARN.value, 0)
        append_event(
            session,
            actor=SYSTEM.audit_name,
            action="invoice.extracted",
            entity_id=invoice.entity_id,
            invoice_id=invoice.id,
            data={
                "extractor": result.extractor,
                "model": result.model,
                "fields": len(scored),
                "below_high": below_high,
                "text_layer": text_layer is not None,
            },
        )

        touch_free = bool(entity.touch_free) and below_high == 0 and not blocks and not warnings
        data = {"below_high": below_high, "open_blocks": blocks, "open_warnings": warnings}
        if touch_free:
            invoice.auto_reviewed = True
            extraction_touch_free(session, invoice, SYSTEM, data={**data, "auto_reviewed": True})
        else:
            extraction_succeeded(session, invoice, SYSTEM, data=data)
        return Outcome(
            route="touch_free" if touch_free else "needs_review",
            fields=len(scored),
            below_high=below_high,
            blocks=blocks,
            warnings=warnings,
        )

    def _store_fields(self, session: Session, invoice: Invoice, scored: list[ScoredField]) -> None:
        # A re-extraction (clerk retry, crash re-run) replaces the previous fields.
        session.execute(delete(InvoiceField).where(InvoiceField.invoice_id == invoice.id))
        for f in scored:
            session.add(
                InvoiceField(
                    invoice_id=invoice.id,
                    entity_id=invoice.entity_id,
                    name=f.name,
                    value=mask(f.value) if f.name in RESTRICTED_FIELDS else f.value,
                    confidence=f.confidence,
                    model_confidence=f.model_confidence,
                    failed_checks=",".join(f.failed_checks) or None,
                    page=f.page,
                )
            )

    def _copy_header(self, invoice: Invoice, result: ExtractionResult) -> None:
        ext = result.invoice
        number = ext.invoice_number.value
        day = parse_date(ext.invoice_date.value)
        invoice.supplier_name = (ext.supplier.name.value or None) and ext.supplier.name.value[:200]
        invoice.supplier_gstin = (normalise_gstin(ext.supplier.gstin.value) or "")[:15] or None
        invoice.invoice_number = number[:64] if number else None
        invoice.number_norm = normalise_invoice_number(number)[:64] if number else None
        invoice.invoice_date = day
        invoice.fy = financial_year_label(day) if day else None
        irn = ext.irn.value
        invoice.irn = irn.strip()[:64] if irn else None
        ts = ext.tax_summary

        def money(value: str | None) -> Money | None:
            amount = parse_amount(value)
            return None if amount is None else Money.round_from(amount)

        invoice.taxable_value = money(ts.subtotal.value)
        heads = [parse_amount(getattr(ts, h).value) for h in ("cgst", "sgst", "utgst", "igst")]
        known = [a for a in heads if a is not None]
        invoice.tax_total = Money.round_from(sum(known)) if known else None
        invoice.grand_total = money(ts.grand_total.value)

    def _raise_once(
        self,
        session: Session,
        invoice: Invoice,
        *,
        rule_id: str,
        severity: FlagSeverity,
        message: str,
        related_invoice_id: int | None = None,
    ) -> None:
        exists = session.scalar(
            select(Flag.id).where(
                Flag.invoice_id == invoice.id,
                Flag.rule_id == rule_id,
                Flag.status == FlagStatus.OPEN.value,
                Flag.message == message,
            )
        )
        if exists is None:
            raise_flag(
                session,
                SYSTEM,
                invoice_id=invoice.id,
                entity_id=invoice.entity_id,
                rule_id=rule_id,
                severity=severity,
                message=message,
                related_invoice_id=related_invoice_id,
            )

    def _raise_tax_flags(self, session: Session, invoice: Invoice, flags: list[TaxFlag]) -> None:
        for f in flags:
            self._raise_once(
                session,
                invoice,
                rule_id=f.rule_id,
                severity=FlagSeverity(f.severity.value),
                message=f"{f.message} {f.fix}",
            )

    def _raise_duplicate_flags(self, session: Session, invoice: Invoice) -> None:
        if not invoice.supplier_gstin:
            return
        stmt = select(Invoice).where(
            Invoice.supplier_gstin == invoice.supplier_gstin,
            Invoice.id != invoice.id,
            Invoice._state != S.DISCARDED.value,
        )
        candidates = session.scalars(_scope(invoice.entity_id).where(stmt, Invoice)).all()
        me = _record(invoice)
        for match in find_duplicates(me, [_record(c) for c in candidates]):
            rule = match.rules[0]
            self._raise_once(
                session,
                invoice,
                rule_id=rule,
                severity=FlagSeverity(match.severity.value),
                message=DUPLICATE_MESSAGES[rule].format(other=match.candidate_id),
                related_invoice_id=int(match.candidate_id),
            )


def _record(invoice: Invoice) -> InvoiceRecord:
    # No file hash: identical files were flagged at intake (FR-13, DUP-EXACT-FILE).
    return InvoiceRecord(
        id=str(invoice.id),
        entity_id=str(invoice.entity_id),
        supplier_gstin=invoice.supplier_gstin,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total=invoice.grand_total.amount if invoice.grand_total is not None else None,
    )
