"""SC-10 Upload and SC-3 Invoice list (basic, no filters) (screens.md §3, FR-10, FR-13, FR-61).

Upload is for clerks: many files at once (drag and drop, or the file picker), to a chosen
entity or auto-assigned, with a result per file. The list shows every invoice in the user's
entities (the header switcher narrows it), through the entity-scoped repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select

from app.accounts.models import Entity
from app.api.deps import Db, SignedIn
from app.api.templating import render
from app.domain.actor import Role
from app.domain.flags import Flag, FlagSeverity, FlagStatus
from app.domain.models import Invoice
from app.domain.states import InvoiceState
from app.intake.service import FileResult, Limits, intake_file
from app.scoping import NotFound

router = APIRouter()

AUTO = "auto"
LIST_LIMIT = 200  # S15 adds filters, search and paging


# --- SC-10 Upload --------------------------------------------------------------------------


def _clerk_entities(db, user) -> list[tuple[int, str]]:
    ids = user.scope.require_any(Role.CLERK)
    rows = db.execute(
        select(Entity.id, Entity.legal_name)
        .where(Entity.id.in_(sorted(ids)))
        .order_by(Entity.legal_name)
    )
    return [(r.id, r.legal_name) for r in rows]


def _default_choice(user, entities: list[tuple[int, str]]) -> str:
    ids = [eid for eid, _ in entities]
    if user.selected_entity_id in ids:
        return str(user.selected_entity_id)
    if len(ids) == 1:
        return str(ids[0])
    return AUTO


def _upload_page(
    request: Request,
    db,
    user,
    *,
    results: list[FileResult] | None = None,
    choice: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    entities = _clerk_entities(db, user)
    settings = request.app.state.settings
    context = {
        "entities": entities,
        "choice": choice or _default_choice(user, entities),
        "results": results,
        "error": error,
        "max_mb": settings.max_upload_bytes // (1024 * 1024),
        "max_pages": settings.max_pages,
        "max_files": settings.max_files_per_upload,
    }
    partial = request.headers.get("hx-request") and not request.headers.get("hx-boosted")
    if partial and request.method == "POST":
        return render(request, "_upload_results.html", context, status_code=status_code)
    return render(request, "upload.html", context, status_code=status_code)


@router.get("/upload", name="upload")
def upload_form(request: Request, user: SignedIn, db: Db) -> Response:
    return _upload_page(request, db, user)


@router.post("/upload", name="upload_submit")
def upload_submit(
    request: Request,
    user: SignedIn,
    db: Db,
    files: Annotated[list[UploadFile] | None, File()] = None,
    entity: Annotated[str, Form()] = AUTO,
) -> Response:
    user.scope.require_any(Role.CLERK)
    entity_id = None
    if entity and entity != AUTO:
        try:
            entity_id = user.scope.require_entity(int(entity), Role.CLERK)
        except ValueError as exc:
            raise NotFound from exc

    chosen = [f for f in (files or []) if f.filename or (f.size or 0) > 0]
    settings = request.app.state.settings
    if not chosen:
        return _upload_page(
            request, db, user, choice=entity, error="Choose at least one file.", status_code=422
        )
    if len(chosen) > settings.max_files_per_upload:
        return _upload_page(
            request,
            db,
            user,
            choice=entity,
            error=(
                f"{len(chosen)} files chosen; upload at most "
                f"{settings.max_files_per_upload} at a time."
            ),
            status_code=422,
        )

    limits = Limits(max_bytes=settings.max_upload_bytes, max_pages=settings.max_pages)
    results = [
        intake_file(
            request.app.state.sessions,
            request.app.state.file_store,
            user.actor,
            file_name=upload.filename,
            stream=upload.file,
            entity_id=entity_id,
            limits=limits,
        )
        for upload in chosen
    ]
    return _upload_page(request, db, user, results=results, choice=entity)


# --- SC-3 Invoice list ---------------------------------------------------------------------


@dataclass(frozen=True)
class InvoiceRow:
    invoice: Invoice
    entity_name: str
    blocks: int
    warnings: int


@router.get("/invoices", name="invoice_list")
def invoice_list(request: Request, user: SignedIn, db: Db) -> Response:
    scope = user.scope
    stmt = (
        scope.select(Invoice)
        .add_columns(Entity.legal_name)
        .join(Entity, Entity.id == Invoice.entity_id)
        .order_by(Invoice.id.desc())
        .limit(LIST_LIMIT + 1)
    )
    found = db.execute(stmt).all()
    more = len(found) > LIST_LIMIT
    found = found[:LIST_LIMIT]
    ids = [inv.id for inv, _ in found]
    open_flags: dict[tuple[int, str], int] = {}
    if ids:
        flag_stmt = scope.where(
            select(Flag.invoice_id, Flag.severity, func.count())
            .where(Flag.invoice_id.in_(ids), Flag.status == FlagStatus.OPEN.value)
            .group_by(Flag.invoice_id, Flag.severity),
            Flag,
        )
        open_flags = {(i, sev): n for i, sev, n in db.execute(flag_stmt)}
    rows = [
        InvoiceRow(
            invoice=inv,
            entity_name=name,
            blocks=open_flags.get((inv.id, FlagSeverity.BLOCK.value), 0),
            warnings=open_flags.get((inv.id, FlagSeverity.WARN.value), 0),
        )
        for inv, name in found
    ]
    return render(
        request,
        "invoices.html",
        {
            "rows": rows,
            "more": more,
            "limit": LIST_LIMIT,
            "can_upload": bool(scope.listing_entity_ids(Role.CLERK)),
            "in_progress": any(
                r.invoice.state in (InvoiceState.RECEIVED, InvoiceState.EXTRACTING) for r in rows
            ),
        },
    )
