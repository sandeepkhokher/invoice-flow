"""SC-40 Admin: entities (FR-1) and SC-41 Admin: users (FR-3).

Only users with `admin` see these pages (403 otherwise). An admin sees and changes only the
entities they administer, and users with access to those entities; anything else is a 404.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.accounts import passwords
from app.accounts.models import Entity, GstRegistration, User
from app.accounts.service import (
    MAX_TIER,
    EntityGrants,
    ValidationError,
    add_registration,
    create_entity,
    create_user,
    load_grants,
    remove_registration,
    set_active,
    set_grants,
    state_name,
    update_entity,
    update_registration,
    visible_users_stmt,
)
from app.api.deps import CurrentUser, Db, SignedIn
from app.api.templating import render
from app.domain.actor import Permission, Role
from app.scoping import NotFound

router = APIRouter(prefix="/admin")


async def read_form(request: Request) -> FormData:
    return await request.form()


Form = Annotated[FormData, Depends(read_form)]


def _field(form: FormData, name: str) -> str:
    value = form.get(name, "")
    return value if isinstance(value, str) else ""


# --- SC-40 entities --------------------------------------------------------------------


def _admin_entities(db: Session, user: CurrentUser) -> list[Entity]:
    user.scope.require_any(Role.ADMIN)
    stmt = user.scope.select(Entity, Role.ADMIN, listing=False).order_by(Entity.legal_name)
    return list(db.scalars(stmt))


def _entities_page(request, db, user, *, errors=None, form=None, status_code=200) -> Response:
    return render(
        request,
        "admin/entities.html",
        {
            "entities": _admin_entities(db, user),
            "state_name": state_name,
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("/entities", name="admin_entities")
def entities(request: Request, user: SignedIn, db: Db) -> Response:
    return _entities_page(request, db, user)


@router.post("/entities", name="admin_entity_create")
def entity_create(request: Request, user: SignedIn, db: Db, form: Form) -> Response:
    user.scope.require_any(Role.ADMIN)
    fields = {"legal_name": _field(form, "legal_name"), "pan": _field(form, "pan")}
    try:
        entity = create_entity(db, user.actor, **fields)
    except ValidationError as exc:
        db.rollback()
        return _entities_page(request, db, user, errors=exc.errors, form=fields, status_code=422)
    db.commit()
    return RedirectResponse(f"/admin/entities/{entity.id}?msg=entity_created", status_code=303)


def _entity_page(request, db, user, entity, *, errors=None, form=None, status_code=200) -> Response:
    return render(
        request,
        "admin/entity.html",
        {
            "entity": entity,
            "state_name": state_name,
            "errors": errors or {},
            "form": form or {"legal_name": entity.legal_name, "pan": entity.pan},
        },
        status_code=status_code,
    )


def _entity(db: Session, user: CurrentUser, entity_id: int) -> Entity:
    user.scope.require_any(Role.ADMIN)
    return user.scope.get(db, Entity, entity_id, Role.ADMIN)


@router.get("/entities/{entity_id}", name="admin_entity")
def entity_detail(request: Request, entity_id: int, user: SignedIn, db: Db) -> Response:
    return _entity_page(request, db, user, _entity(db, user, entity_id))


@router.post("/entities/{entity_id}", name="admin_entity_update")
def entity_update(request: Request, entity_id: int, user: SignedIn, db: Db, form: Form) -> Response:
    entity = _entity(db, user, entity_id)
    fields = {"legal_name": _field(form, "legal_name"), "pan": _field(form, "pan")}
    try:
        update_entity(db, user.actor, entity, **fields)
    except ValidationError as exc:
        db.rollback()
        entity = _entity(db, user, entity_id)
        return _entity_page(
            request, db, user, entity, errors=exc.errors, form=fields, status_code=422
        )
    db.commit()
    return RedirectResponse(f"/admin/entities/{entity_id}?msg=entity_saved", status_code=303)


@router.post("/entities/{entity_id}/registrations", name="admin_registration_add")
def registration_add(
    request: Request, entity_id: int, user: SignedIn, db: Db, form: Form
) -> Response:
    entity = _entity(db, user, entity_id)
    fields = {"gstin": _field(form, "gstin"), "address": _field(form, "address")}
    try:
        add_registration(db, user.actor, entity, **fields)
    except ValidationError as exc:
        db.rollback()
        entity = _entity(db, user, entity_id)
        reg_form = {"legal_name": entity.legal_name, "pan": entity.pan, **fields}
        return _entity_page(
            request, db, user, entity, errors=exc.errors, form=reg_form, status_code=422
        )
    db.commit()
    return RedirectResponse(f"/admin/entities/{entity_id}?msg=registration_added", status_code=303)


def _registration(db: Session, user: CurrentUser, entity_id: int, reg_id: int):
    entity = _entity(db, user, entity_id)
    reg = user.scope.get(db, GstRegistration, reg_id, Role.ADMIN)
    if reg.entity_id != entity.id:
        raise NotFound
    return entity, reg


@router.post("/entities/{entity_id}/registrations/{reg_id}", name="admin_registration_update")
def registration_update(
    request: Request, entity_id: int, reg_id: int, user: SignedIn, db: Db, form: Form
) -> Response:
    entity, reg = _registration(db, user, entity_id, reg_id)
    try:
        update_registration(db, user.actor, reg, address=_field(form, "address"))
    except ValidationError as exc:
        db.rollback()
        entity, _ = _registration(db, user, entity_id, reg_id)
        errors = {f"address:{reg_id}": exc.errors.get("address", "")}
        return _entity_page(request, db, user, entity, errors=errors, status_code=422)
    db.commit()
    return RedirectResponse(f"/admin/entities/{entity_id}?msg=registration_saved", status_code=303)


@router.post(
    "/entities/{entity_id}/registrations/{reg_id}/remove", name="admin_registration_remove"
)
def registration_remove(entity_id: int, reg_id: int, user: SignedIn, db: Db) -> Response:
    _, reg = _registration(db, user, entity_id, reg_id)
    remove_registration(db, user.actor, reg)
    db.commit()
    return RedirectResponse(
        f"/admin/entities/{entity_id}?msg=registration_removed", status_code=303
    )


# --- SC-41 users -----------------------------------------------------------------------


def _managed(db: Session, user: CurrentUser) -> list[Entity]:
    return _admin_entities(db, user)


def parse_grants(form: FormData, entity_ids) -> tuple[dict[int, EntityGrants], dict[str, str]]:
    """Read the grants grid: `role:<entity>` (checkboxes), `tier:<entity>`, `perm:<entity>`."""
    grants: dict[int, EntityGrants] = {}
    errors: dict[str, str] = {}
    for eid in entity_ids:
        try:
            roles = frozenset(Role(r) for r in form.getlist(f"role:{eid}"))
            perms = frozenset(Permission(p) for p in form.getlist(f"perm:{eid}"))
        except ValueError:
            errors[f"role:{eid}"] = "Unknown role or permission."
            continue
        tier_raw = _field(form, f"tier:{eid}").strip()
        tier = None
        if Role.APPROVER in roles:
            if not tier_raw.isdigit():
                errors[f"tier:{eid}"] = f"Give approvers a tier from 1 to {MAX_TIER}."
                continue
            tier = int(tier_raw)
        grants[eid] = EntityGrants(roles=roles, tier=tier, permissions=perms)
    return grants, errors


def _grid(entities: list[Entity], grants: dict[int, EntityGrants]) -> list[dict]:
    return [{"entity": e, "grants": grants.get(e.id, EntityGrants())} for e in entities]


def _users_page(request, db, user, *, errors=None, form=None, grants=None, status_code=200):
    managed = _managed(db, user)
    users = list(db.scalars(visible_users_stmt(e.id for e in managed).order_by(User.name, User.id)))
    return render(
        request,
        "admin/users.html",
        {
            "users": users,
            "grid": _grid(managed, grants or {}),
            "roles": list(Role),
            "permissions": list(Permission),
            "max_tier": MAX_TIER,
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("/users", name="admin_users")
def users(request: Request, user: SignedIn, db: Db) -> Response:
    return _users_page(request, db, user)


@router.post("/users", name="admin_user_invite")
def user_invite(request: Request, user: SignedIn, db: Db, form: Form) -> Response:
    managed = _managed(db, user)
    managed_ids = [e.id for e in managed]
    fields = {"email": _field(form, "email"), "name": _field(form, "name")}
    grants, errors = parse_grants(form, managed_ids)
    password = passwords.generate_password()
    try:
        if errors:
            raise ValidationError(errors)
        new_user = create_user(db, user.actor, password=password, **fields)
        set_grants(db, user.actor, new_user, grants, managed_entity_ids=managed_ids)
    except ValidationError as exc:
        db.rollback()
        return _users_page(
            request, db, user, errors=exc.errors, form=fields, grants=grants, status_code=422
        )
    db.commit()
    # Shown once, on this response only; never stored in clear, logged or put in a URL.
    return _user_page(request, db, user, new_user, temp_password=password, status_code=201)


def _target_user(db: Session, user: CurrentUser, user_id: int) -> tuple[User, list[Entity]]:
    managed = _managed(db, user)
    stmt = visible_users_stmt(e.id for e in managed).where(User.id == user_id)
    target = db.scalars(stmt).first()
    if target is None:
        raise NotFound
    return target, managed


def _user_page(
    request, db, user, target, *, errors=None, grants=None, temp_password=None, status_code=200
):
    managed = _managed(db, user)
    all_grants = load_grants(db, target.id)
    managed_ids = {e.id for e in managed}
    other_entities = len(set(all_grants) - managed_ids)
    return render(
        request,
        "admin/user.html",
        {
            "target": target,
            "grid": _grid(managed, grants if grants is not None else all_grants),
            "roles": list(Role),
            "permissions": list(Permission),
            "max_tier": MAX_TIER,
            "other_entities": other_entities,
            "temp_password": temp_password,
            "errors": errors or {},
        },
        status_code=status_code,
    )


@router.get("/users/{user_id}", name="admin_user")
def user_detail(request: Request, user_id: int, user: SignedIn, db: Db) -> Response:
    target, _ = _target_user(db, user, user_id)
    return _user_page(request, db, user, target)


@router.post("/users/{user_id}/grants", name="admin_user_grants")
def user_grants(request: Request, user_id: int, user: SignedIn, db: Db, form: Form) -> Response:
    target, managed = _target_user(db, user, user_id)
    managed_ids = [e.id for e in managed]
    grants, errors = parse_grants(form, managed_ids)
    try:
        if errors:
            raise ValidationError(errors)
        set_grants(db, user.actor, target, grants, managed_entity_ids=managed_ids)
    except ValidationError as exc:
        db.rollback()
        target, _ = _target_user(db, user, user_id)
        return _user_page(
            request, db, user, target, errors=exc.errors, grants=grants, status_code=422
        )
    db.commit()
    if target.id == user.id:  # your own roles changed; your admin pages may have too
        return RedirectResponse("/?msg=grants_saved", status_code=303)
    return RedirectResponse(f"/admin/users/{user_id}?msg=grants_saved", status_code=303)


@router.post("/users/{user_id}/active", name="admin_user_active")
def user_active(request: Request, user_id: int, user: SignedIn, db: Db, form: Form) -> Response:
    target, managed = _target_user(db, user, user_id)
    active = _field(form, "active") == "1"
    try:
        set_active(db, user.actor, target, active, managed_entity_ids=[e.id for e in managed])
    except ValidationError as exc:
        db.rollback()
        target, _ = _target_user(db, user, user_id)
        return _user_page(request, db, user, target, errors=exc.errors, status_code=422)
    db.commit()
    msg = "user_reactivated" if active else "user_deactivated"
    return RedirectResponse(f"/admin/users/{user_id}?msg={msg}", status_code=303)
