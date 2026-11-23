"""SC-41 Admin: users and per-entity role grants (FR-3, ticket #10)."""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts.models import PermissionGrant, RoleGrant, User
from app.accounts.service import EntityGrants, ValidationError, actor_for, validate_grants
from app.audit.models import AuditEvent
from app.domain.actor import Permission, Role
from tests.conftest import add_user, csrf_of, grants, login, signed_in


@pytest.fixture
def admin(app, world):
    return signed_in(app, "admin@a.example")


def post(client, url, data):
    return client.post(
        url, data={**data, "csrf_token": csrf_of(client, "/admin/users")}, follow_redirects=False
    )


def uid(app, email):
    with app.state.sessions() as db:
        return db.scalar(select(User.id).where(User.email == email))


def grants_of(app, email):
    with app.state.sessions() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        roles = {
            (g.entity_id, g.role, g.tier)
            for g in db.scalars(select(RoleGrant).where(RoleGrant.user_id == user_id))
        }
        perms = {
            (p.entity_id, p.permission)
            for p in db.scalars(select(PermissionGrant).where(PermissionGrant.user_id == user_id))
        }
    return roles, perms


# --- who sees SC-41 ----------------------------------------------------------------------


def test_admin_sees_users_of_their_entities_only(app, admin):
    page = admin.get("/admin/users")
    assert page.status_code == 200
    assert "clerk@a.example" in page.text and "auditor@a.example" in page.text
    assert "clerk@b.example" not in page.text and "admin@b.example" not in page.text


@pytest.mark.parametrize("who", ["clerk", "approver", "accountant", "auditor"])
def test_non_admins_get_403(app, world, who):
    client = signed_in(app, f"{who}@a.example")
    assert client.get("/admin/users").status_code == 403
    assert client.get(f"/admin/users/{world['clerk']}").status_code == 403


def test_user_of_another_entity_is_404(app, admin, world):
    assert admin.get(f"/admin/users/{world['clerk']}").status_code == 200
    assert admin.get(f"/admin/users/{world['clerk_b']}").status_code == 404
    resp = post(admin, f"/admin/users/{world['clerk_b']}/grants", {"role:1": "clerk"})
    assert resp.status_code == 404


# --- invite ------------------------------------------------------------------------------


def test_invite_shows_a_one_time_password_that_works(app, admin):
    resp = post(
        admin,
        "/admin/users",
        {
            "name": "New Person",
            "email": "New@A.example",
            "role:1": ["clerk", "approver"],
            "tier:1": "2",
            "perm:1": "flags:override",
        },
    )
    assert resp.status_code == 201
    password = re.search(r'<p class="mono"[^>]*>([^<]+)</p>', resp.text).group(1)
    assert len(password) >= 12
    roles, perms = grants_of(app, "new@a.example")
    assert roles == {(1, "clerk", None), (1, "approver", 2)}
    assert perms == {(1, "flags:override")}
    # The password is not stored in clear or written to the audit trail.
    with app.state.sessions() as db:
        assert password not in db.scalar(
            select(User.password_hash).where(User.email == "new@a.example")
        )
        assert all(password not in e.data_json for e in db.scalars(select(AuditEvent)))
    assert login(TestClient(app), "new@a.example", password).status_code == 303


@pytest.mark.parametrize(
    "data,message",
    [
        ({"name": "X", "email": "not-an-email"}, "Enter an email address"),
        ({"name": "", "email": "x@a.example"}, "Enter the person"),
        ({"name": "Dup", "email": "clerk@a.example"}, "already exists"),
        ({"name": "X", "email": "x@a.example", "role:1": "approver"}, "tier from 1 to 9"),
        ({"name": "X", "email": "x@a.example", "perm:1": "bank_details:view"}, "need a role"),
    ],
)
def test_invite_errors(app, admin, data, message):
    resp = post(admin, "/admin/users", data)
    assert resp.status_code == 422
    assert message in resp.text
    with app.state.sessions() as db:
        assert db.scalar(select(User).where(User.email == "x@a.example")) is None


# --- grants ------------------------------------------------------------------------------


def test_save_grants_replaces_roles_and_permissions(app, admin, world):
    url = f"/admin/users/{world['clerk']}/grants"
    resp = post(
        admin,
        url,
        {"role:1": ["approver", "auditor"], "tier:1": "3", "perm:1": "bank_details:view"},
    )
    assert resp.status_code == 303
    roles, perms = grants_of(app, "clerk@a.example")
    assert roles == {(1, "approver", 3), (1, "auditor", None)}
    assert perms == {(1, "bank_details:view")}
    with app.state.sessions() as db:
        actor = actor_for(db, world["clerk"])
        event = db.scalars(select(AuditEvent).order_by(AuditEvent.seq.desc())).first()
    assert actor.roles_by_entity == {1: {Role.APPROVER, Role.AUDITOR}}
    assert actor.approver_tier_by_entity == {1: 3}
    assert actor.has_permission(1, Permission.BANK_DETAILS_VIEW)
    assert not actor.has_permission(1, Permission.FLAGS_OVERRIDE)
    assert event.action == "user.grants_changed" and event.entity_id == 1


def test_grants_on_unmanaged_entities_are_not_touched(app, admin, world, sessions):
    both = add_user(sessions, "both@x.example", {1: grants(Role.CLERK), 2: grants(Role.AUDITOR)})
    resp = post(admin, f"/admin/users/{both}/grants", {"role:1": "accountant", "role:2": "admin"})
    assert resp.status_code == 303
    roles, _ = grants_of(app, "both@x.example")
    assert roles == {(1, "accountant", None), (2, "auditor", None)}
    page = admin.get(f"/admin/users/{both}").text
    assert "1 other entity you do not administer" in page
    assert "Entity Two" not in page


def test_removing_all_roles_hides_entity_data(app, admin, world):
    assert post(admin, f"/admin/users/{world['clerk']}/grants", {}).status_code == 303
    assert grants_of(app, "clerk@a.example") == (set(), set())
    clerk = signed_in(app, "clerk@a.example")
    assert "no access to any entity" in clerk.get("/").text


def test_entity_keeps_at_least_one_admin(app, admin, world):
    resp = post(admin, f"/admin/users/{world['admin']}/grants", {"role:1": "auditor"})
    assert resp.status_code == 422
    assert "must keep at least one active admin" in resp.text
    roles, _ = grants_of(app, "admin@a.example")
    assert roles == {(1, "admin", None)}


def test_tier_rules():
    validate_grants({1: EntityGrants(frozenset({Role.APPROVER}), tier=1)})
    for bad in (
        EntityGrants(frozenset({Role.APPROVER}), tier=None),
        EntityGrants(frozenset({Role.APPROVER}), tier=10),
        EntityGrants(frozenset({Role.CLERK}), tier=1),
        EntityGrants(frozenset(), permissions=frozenset({Permission.FLAGS_OVERRIDE})),
    ):
        with pytest.raises(ValidationError):
            validate_grants({1: bad})


# --- deactivate --------------------------------------------------------------------------


def test_deactivate_and_reactivate(app, admin, world):
    victim = signed_in(app, "clerk@a.example")
    url = f"/admin/users/{world['clerk']}/active"
    assert post(admin, url, {"active": "0"}).status_code == 303
    assert victim.get("/", follow_redirects=False).status_code == 303  # session ended
    assert login(TestClient(app), "clerk@a.example").status_code == 401
    assert "Deactivated" in admin.get("/admin/users").text
    assert post(admin, url, {"active": "1"}).status_code == 303
    assert login(TestClient(app), "clerk@a.example").status_code == 303
    with app.state.sessions() as db:
        actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)).all()
    assert "user.deactivated" in actions and "user.reactivated" in actions


def test_cannot_deactivate_yourself(app, admin, world):
    resp = post(admin, f"/admin/users/{world['admin']}/active", {"active": "0"})
    assert resp.status_code == 422 and "your own account" in resp.text


def test_cannot_deactivate_a_user_with_other_entities(app, admin, sessions):
    both = add_user(sessions, "both@x.example", {1: grants(Role.CLERK), 2: grants(Role.AUDITOR)})
    resp = post(admin, f"/admin/users/{both}/active", {"active": "0"})
    assert resp.status_code == 422 and "entities you do not administer" in resp.text
