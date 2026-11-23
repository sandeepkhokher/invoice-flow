"""Entities, registrations, users and grants: validation, changes and their audit events.

Callers check *whether* the actor may act (the routes use `EntityScope`); these functions
validate the input, make the change and write the audit event in the caller's transaction
(approval-and-audit §5). Nothing here commits.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.accounts import passwords
from app.accounts.models import (
    Entity,
    GstRegistration,
    PermissionGrant,
    RoleGrant,
    User,
    UserSession,
)
from app.audit.chain import append_event
from app.domain.actor import Actor, Permission, Role
from app.tax.gstin import gstin_problem, normalise_gstin, state_codes

PAN_PATTERN = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_TIER = 9


class ValidationError(ValueError):
    """Input the user can fix. `errors` maps a form field name to a message."""

    def __init__(self, errors: Mapping[str, str]) -> None:
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = dict(errors)


@dataclass(frozen=True)
class EntityGrants:
    """One user's grants on one entity."""

    roles: frozenset[Role] = frozenset()
    tier: int | None = None
    permissions: frozenset[Permission] = field(default_factory=frozenset)

    @property
    def empty(self) -> bool:
        return not self.roles and not self.permissions


def _audit(db: Session, actor: Actor, action: str, *, entity_id: int | None = None, **data: Any):
    append_event(db, actor=actor.audit_name, action=action, entity_id=entity_id, data=data)


# --- entities and GST registrations (FR-1) ------------------------------------------------


def _clean_entity_fields(legal_name: str, pan: str) -> tuple[str, str]:
    legal_name = " ".join(legal_name.split())
    pan = pan.strip().upper()
    errors = {}
    if not legal_name:
        errors["legal_name"] = "Enter the entity's legal name."
    elif len(legal_name) > 200:
        errors["legal_name"] = "Use at most 200 characters."
    if not PAN_PATTERN.fullmatch(pan):
        errors["pan"] = "Enter a 10-character PAN, for example AAPFU0939F."
    if errors:
        raise ValidationError(errors)
    return legal_name, pan


def _check_pan_free(db: Session, pan: str, entity_id: int | None = None) -> None:
    other = db.scalar(select(Entity.id).where(Entity.pan == pan))
    if other is not None and other != entity_id:
        raise ValidationError({"pan": "An entity with this PAN already exists."})


def create_entity(db: Session, actor: Actor, *, legal_name: str, pan: str) -> Entity:
    """Create an entity. A user who creates one becomes its admin, so they can manage it."""
    legal_name, pan = _clean_entity_fields(legal_name, pan)
    _check_pan_free(db, pan)
    entity = Entity(legal_name=legal_name, pan=pan)
    db.add(entity)
    db.flush()
    _audit(db, actor, "entity.created", entity_id=entity.id, legal_name=legal_name, pan=pan)
    if actor.user_id is not None:
        db.add(RoleGrant(user_id=actor.user_id, entity_id=entity.id, role=Role.ADMIN.value))
        db.flush()
        _audit(
            db,
            actor,
            "user.grants_changed",
            entity_id=entity.id,
            user_id=actor.user_id,
            roles=[Role.ADMIN.value],
            tier=None,
            permissions=[],
        )
    return entity


def update_entity(db: Session, actor: Actor, entity: Entity, *, legal_name: str, pan: str):
    legal_name, pan = _clean_entity_fields(legal_name, pan)
    if pan != entity.pan:
        if entity.registrations:
            raise ValidationError(
                {"pan": "The PAN cannot change while the entity has GST registrations."}
            )
        _check_pan_free(db, pan, entity.id)
    changes = {}
    if legal_name != entity.legal_name:
        changes["legal_name"] = {"old": entity.legal_name, "new": legal_name}
    if pan != entity.pan:
        changes["pan"] = {"old": entity.pan, "new": pan}
    if not changes:
        return entity
    entity.legal_name, entity.pan = legal_name, pan
    db.flush()
    _audit(db, actor, "entity.updated", entity_id=entity.id, changes=changes)
    return entity


def _clean_address(address: str) -> str:
    address = address.strip()
    if not address:
        raise ValidationError({"address": "Enter the registered address."})
    if len(address) > 1000:
        raise ValidationError({"address": "Use at most 1,000 characters."})
    return address


def add_registration(
    db: Session, actor: Actor, entity: Entity, *, gstin: str, address: str
) -> GstRegistration:
    """Add a GST registration. The GSTIN must pass the S2 checks, carry the entity's PAN and
    not be registered to any entity yet (FR-1)."""
    gstin = normalise_gstin(gstin)
    errors = {}
    problem = gstin_problem(gstin)
    owner = None
    if not problem:
        owner = db.scalar(select(GstRegistration.entity_id).where(GstRegistration.gstin == gstin))
    if problem:
        errors["gstin"] = (
            f"This GSTIN is not valid: {problem}. Copy it from the registration certificate."
        )
    elif owner == entity.id:
        errors["gstin"] = "This GSTIN is already registered to this entity."
    elif owner is not None:
        # FR-1: a GSTIN cannot belong to two entities. Say no more about the other one.
        errors["gstin"] = "This GSTIN is already registered to another entity."
    elif gstin[2:12] != entity.pan:
        errors["gstin"] = (
            f"Characters 3-12 of a GSTIN are the holder's PAN; this one does not match the "
            f"entity's PAN {entity.pan}."
        )
    try:
        address = _clean_address(address)
    except ValidationError as exc:
        errors.update(exc.errors)
    if errors:
        raise ValidationError(errors)
    reg = GstRegistration(entity_id=entity.id, gstin=gstin, state_code=gstin[:2], address=address)
    db.add(reg)
    db.flush()
    _audit(
        db,
        actor,
        "gst_registration.added",
        entity_id=entity.id,
        registration_id=reg.id,
        gstin=gstin,
        state_code=reg.state_code,
    )
    return reg


def update_registration(db: Session, actor: Actor, reg: GstRegistration, *, address: str):
    address = _clean_address(address)
    if address == reg.address:
        return reg
    reg.address = address
    db.flush()
    _audit(
        db,
        actor,
        "gst_registration.updated",
        entity_id=reg.entity_id,
        registration_id=reg.id,
        gstin=reg.gstin,
    )
    return reg


def remove_registration(db: Session, actor: Actor, reg: GstRegistration) -> None:
    entity_id, reg_id, gstin = reg.entity_id, reg.id, reg.gstin
    db.delete(reg)
    db.flush()
    _audit(
        db,
        actor,
        "gst_registration.removed",
        entity_id=entity_id,
        registration_id=reg_id,
        gstin=gstin,
    )


def state_name(code: str) -> str:
    sc = state_codes().get(code)
    return sc.name if sc else code


# --- users and grants (FR-3) --------------------------------------------------------------


def normalise_email(email: str) -> str:
    return email.strip().lower()


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalise_email(email)))


def create_user(db: Session, actor: Actor, *, email: str, name: str, password: str) -> User:
    email = normalise_email(email)
    name = " ".join(name.split())
    errors = {}
    if not EMAIL_PATTERN.fullmatch(email) or len(email) > 254:
        errors["email"] = "Enter an email address, for example name@example.com."
    elif find_user_by_email(db, email) is not None:
        errors["email"] = "A user with this email already exists."
    if not name:
        errors["name"] = "Enter the person's name."
    elif len(name) > 200:
        errors["name"] = "Use at most 200 characters."
    if problem := passwords.password_problem(password):
        errors["password"] = problem
    if errors:
        raise ValidationError(errors)
    user = User(email=email, name=name, password_hash=passwords.hash_password(password))
    db.add(user)
    db.flush()
    _audit(db, actor, "user.created", user_id=user.id)
    return user


def load_grants(db: Session, user_id: int) -> dict[int, EntityGrants]:
    roles: dict[int, set[Role]] = {}
    tiers: dict[int, int] = {}
    perms: dict[int, set[Permission]] = {}
    for g in db.scalars(select(RoleGrant).where(RoleGrant.user_id == user_id)):
        roles.setdefault(g.entity_id, set()).add(Role(g.role))
        if g.tier is not None:
            tiers[g.entity_id] = g.tier
    for p in db.scalars(select(PermissionGrant).where(PermissionGrant.user_id == user_id)):
        perms.setdefault(p.entity_id, set()).add(Permission(p.permission))
    return {
        eid: EntityGrants(
            roles=frozenset(roles.get(eid, ())),
            tier=tiers.get(eid),
            permissions=frozenset(perms.get(eid, ())),
        )
        for eid in roles.keys() | perms.keys()
    }


def actor_for(db: Session, user_id: int) -> Actor:
    """The domain `Actor` for a user, from their grants (used by every transition)."""
    grants = load_grants(db, user_id)
    return Actor.user(
        user_id,
        {eid: g.roles for eid, g in grants.items() if g.roles},
        permissions_by_entity={eid: g.permissions for eid, g in grants.items() if g.roles},
        approver_tier_by_entity={eid: g.tier for eid, g in grants.items() if g.tier is not None},
    )


def validate_grants(grants: Mapping[int, EntityGrants]) -> None:
    errors = {}
    for eid, g in grants.items():
        if Role.APPROVER in g.roles:
            if g.tier is None or not 1 <= g.tier <= MAX_TIER:
                errors[f"tier:{eid}"] = f"Give approvers a tier from 1 to {MAX_TIER}."
        elif g.tier is not None:
            errors[f"tier:{eid}"] = "Only approvers have a tier."
        if g.permissions and not g.roles:
            errors[f"perm:{eid}"] = "Extra permissions need a role on the entity."
    if errors:
        raise ValidationError(errors)


def _admin_count(db: Session, entity_id: int) -> int:
    return db.scalar(
        select(func.count())
        .select_from(RoleGrant)
        .join(User, User.id == RoleGrant.user_id)
        .where(
            RoleGrant.entity_id == entity_id,
            RoleGrant.role == Role.ADMIN.value,
            User.is_active.is_(True),
        )
    )


def _entities_with_admins(db: Session, entity_ids: Iterable[int]) -> set[int]:
    return {eid for eid in entity_ids if _admin_count(db, eid) > 0}


def _keep_an_admin(db: Session, had_admin: Iterable[int]) -> None:
    """A change may not remove the last active admin of an entity that had one."""
    db.flush()
    for eid in sorted(had_admin):
        if _admin_count(db, eid) == 0:
            entity = db.get(Entity, eid)
            label = entity.legal_name if entity else f"entity {eid}"
            raise ValidationError({"grants": f"{label} must keep at least one active admin."})


def set_grants(
    db: Session,
    actor: Actor,
    user: User,
    grants: Mapping[int, EntityGrants],
    *,
    managed_entity_ids: Iterable[int],
) -> None:
    """Replace `user`'s grants on the entities the acting admin manages (and only those)."""
    managed = frozenset(managed_entity_ids)
    if set(grants) - managed:
        raise ValidationError({"grants": "You can only grant access to entities you administer."})
    validate_grants(grants)
    before = load_grants(db, user.id)
    had_admin = _entities_with_admins(db, managed)
    for eid in sorted(managed):
        new = grants.get(eid, EntityGrants())
        old = before.get(eid, EntityGrants())
        if new == old:
            continue
        db.execute(
            delete(RoleGrant).where(RoleGrant.user_id == user.id, RoleGrant.entity_id == eid)
        )
        db.execute(
            delete(PermissionGrant).where(
                PermissionGrant.user_id == user.id, PermissionGrant.entity_id == eid
            )
        )
        for role in sorted(new.roles):
            tier = new.tier if role == Role.APPROVER else None
            db.add(RoleGrant(user_id=user.id, entity_id=eid, role=role.value, tier=tier))
        for perm in sorted(new.permissions):
            db.add(PermissionGrant(user_id=user.id, entity_id=eid, permission=perm.value))
        _audit(
            db,
            actor,
            "user.grants_changed",
            entity_id=eid,
            user_id=user.id,
            roles=sorted(r.value for r in new.roles),
            tier=new.tier if Role.APPROVER in new.roles else None,
            permissions=sorted(p.value for p in new.permissions),
        )
    _keep_an_admin(db, had_admin)


def set_active(
    db: Session, actor: Actor, user: User, active: bool, *, managed_entity_ids: Iterable[int]
) -> None:
    """Deactivate or reactivate a login. The acting admin must administer every entity the
    user has a grant on, because a login is shared across entities."""
    if user.is_active == active:
        return
    if actor.user_id == user.id:
        raise ValidationError({"active": "You cannot deactivate your own account."})
    outside = set(load_grants(db, user.id)) - set(managed_entity_ids)
    if outside:
        raise ValidationError(
            {
                "active": "This user also has access to entities you do not administer. "
                "Remove their roles here instead, or ask an admin of those entities."
            }
        )
    had_admin = _entities_with_admins(db, load_grants(db, user.id))
    user.is_active = active
    if not active:
        db.execute(delete(UserSession).where(UserSession.user_id == user.id))
        _keep_an_admin(db, had_admin)
    db.flush()
    _audit(db, actor, "user.reactivated" if active else "user.deactivated", user_id=user.id)


def visible_users_stmt(managed_entity_ids: Iterable[int]):
    """Users an admin may see: anyone with a grant on an entity they manage, plus users with
    no grant anywhere (just invited, or all access removed). Nobody else's existence leaks."""
    ids = sorted(managed_entity_ids)
    granted = select(RoleGrant.user_id).where(RoleGrant.entity_id.in_(ids))
    permitted = select(PermissionGrant.user_id).where(PermissionGrant.entity_id.in_(ids))
    any_role = select(RoleGrant.user_id)
    any_perm = select(PermissionGrant.user_id)
    return select(User).where(
        or_(
            User.id.in_(granted),
            User.id.in_(permitted),
            ~User.id.in_(any_role) & ~User.id.in_(any_perm),
        )
    )


def update_name(db: Session, actor: Actor, user: User, name: str) -> None:
    name = " ".join(name.split())
    if not name:
        raise ValidationError({"name": "Enter your name."})
    if len(name) > 200:
        raise ValidationError({"name": "Use at most 200 characters."})
    if name != user.name:
        user.name = name
        db.flush()
        _audit(db, actor, "user.profile_updated", user_id=user.id)


def change_password(
    db: Session,
    actor: Actor,
    user: User,
    *,
    current: str,
    new: str,
    confirm: str,
    keep_session_hash: str | None = None,
) -> None:
    """SC-6: change your own password. Signs out every other session of the user."""
    errors = {}
    if not passwords.verify_password(user.password_hash, current):
        errors["current_password"] = "That is not your current password."  # noqa: S105
    if problem := passwords.password_problem(new):
        errors["new_password"] = problem
    elif new != confirm:
        errors["confirm_password"] = "The two new passwords do not match."  # noqa: S105
    if errors:
        raise ValidationError(errors)
    set_password(db, actor, user, new, keep_session_hash=keep_session_hash)


def set_password(
    db: Session, actor: Actor, user: User, new: str, *, keep_session_hash: str | None = None
) -> None:
    user.password_hash = passwords.hash_password(new)
    user.failed_logins = 0
    user.locked_until = None
    stmt = delete(UserSession).where(UserSession.user_id == user.id)
    if keep_session_hash is not None:
        stmt = stmt.where(UserSession.token_hash != keep_session_hash)
    db.execute(stmt)
    db.flush()
    _audit(db, actor, "user.password_changed", user_id=user.id)
