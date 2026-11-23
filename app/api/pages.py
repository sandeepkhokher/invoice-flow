"""SC-2 My work, SC-6 Account, and the header's entity switcher (screens.md §1)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select

from app.accounts.models import Entity, User, UserSession
from app.accounts.service import (
    ValidationError,
    change_password,
    load_grants,
    update_name,
)
from app.api.deps import Db, SignedIn, safe_next
from app.api.templating import render
from app.domain.actor import Role
from app.scoping import NotFound

router = APIRouter()


@dataclass(frozen=True)
class TaskCard:
    title: str
    empty: str


# SC-2 task cards per role. S5 builds the empty cards; S15 adds counts and ages.
CARDS: tuple[tuple[Role, TaskCard], ...] = (
    (
        Role.CLERK,
        TaskCard(
            "To review",
            "No invoices to review. Invoices appear here after they are uploaded and read.",
        ),
    ),
    (
        Role.APPROVER,
        TaskCard(
            "To approve",
            "Nothing waiting for your approval. Invoices appear here when a clerk submits them.",
        ),
    ),
    (
        Role.ACCOUNTANT,
        TaskCard("To export", "No approved invoices to export. Approved invoices appear here."),
    ),
    (
        Role.ACCOUNTANT,
        TaskCard("To mark paid", "No exported invoices waiting for payment."),
    ),
    (
        Role.ADMIN,
        TaskCard("Quarantine", "No quarantined emails. Mail from unknown senders appears here."),
    ),
)


def cards_for(roles: frozenset[Role]) -> list[TaskCard]:
    return [card for role, card in CARDS if role in roles]


@router.get("/", name="my_work")
def my_work(request: Request, user: SignedIn) -> Response:
    roles = user.scope.roles()
    return render(
        request,
        "my_work.html",
        {"cards": cards_for(roles), "has_access": bool(user.actor.roles_by_entity)},
    )


@router.post("/switch-entity", name="switch_entity")
def switch_entity(
    user: SignedIn,
    db: Db,
    entity_id: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    """Pick one entity, or "" for all my entities. An entity without a grant is a 404."""
    selected = None
    if entity_id:
        try:
            selected = user.scope.require_entity(int(entity_id))
        except ValueError as exc:
            raise NotFound from exc
    sess = db.get(UserSession, user.session_hash)
    sess.entity_id = selected
    db.commit()
    target = safe_next(next)
    sep = "&" if "?" in target else "?"
    return RedirectResponse(f"{target}{sep}msg=entity_switched", status_code=303)


def _grants_rows(db, user_id: int) -> list[dict]:
    grants = load_grants(db, user_id)
    rows_ = db.execute(select(Entity.id, Entity.legal_name).where(Entity.id.in_(list(grants))))
    names = {r.id: r.legal_name for r in rows_}
    rows = []
    for eid, g in sorted(grants.items(), key=lambda kv: names.get(kv[0], "")):
        rows.append(
            {
                "entity": names.get(eid, ""),
                "roles": [
                    f"approver (tier {g.tier})" if r == Role.APPROVER else r.value
                    for r in sorted(g.roles)
                ],
                "permissions": sorted(p.value for p in g.permissions),
            }
        )
    return rows


def _account_page(request, db, user, *, errors=None, form=None, status_code=200) -> Response:
    return render(
        request,
        "account.html",
        {"grants": _grants_rows(db, user.id), "errors": errors or {}, "form": form or {}},
        status_code=status_code,
    )


@router.get("/account", name="account")
def account(request: Request, user: SignedIn, db: Db) -> Response:
    return _account_page(request, db, user, form={"name": user.name})


@router.post("/account/profile", name="account_profile")
def account_profile(
    request: Request, user: SignedIn, db: Db, name: Annotated[str, Form()] = ""
) -> Response:
    row = db.get(User, user.id)
    try:
        update_name(db, user.actor, row, name)
    except ValidationError as exc:
        db.rollback()
        return _account_page(
            request, db, user, errors=exc.errors, form={"name": name}, status_code=422
        )
    db.commit()
    return RedirectResponse("/account?msg=profile_saved", status_code=303)


@router.post("/account/password", name="account_password")
def account_password(
    request: Request,
    user: SignedIn,
    db: Db,
    current_password: Annotated[str, Form()] = "",
    new_password: Annotated[str, Form()] = "",
    confirm_password: Annotated[str, Form()] = "",
) -> Response:
    row = db.get(User, user.id)
    try:
        change_password(
            db,
            user.actor,
            row,
            current=current_password,
            new=new_password,
            confirm=confirm_password,
            keep_session_hash=user.session_hash,
        )
    except ValidationError as exc:
        db.rollback()
        return _account_page(
            request, db, user, errors=exc.errors, form={"name": user.name}, status_code=422
        )
    db.commit()
    return RedirectResponse("/account?msg=password_changed", status_code=303)
