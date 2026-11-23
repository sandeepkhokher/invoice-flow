"""SC-40 Admin: entities and GST registrations (FR-1, ticket #10)."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.accounts.models import Entity, GstRegistration, RoleGrant
from app.accounts.service import ValidationError, add_registration, create_entity
from app.audit.models import AuditEvent
from app.domain.actor import Actor, Role
from app.tax.gstin import check_character
from tests.conftest import csrf_of, signed_in

PAN_ONE = "ZXCPA0001A"  # entity 1 in the `world` fixture
PAN_TWO = "ZXCPB0002B"


def gstin(state: str, pan: str) -> str:
    first14 = f"{state}{pan}1Z"
    return first14 + check_character(first14)


def bad_check(g: str) -> str:
    return g[:14] + ("A" if g[14] != "A" else "B")


@pytest.fixture
def admin(app, world):
    return signed_in(app, "admin@a.example")


def post(client, url, data):
    return client.post(
        url, data={**data, "csrf_token": csrf_of(client, "/admin/entities")}, follow_redirects=False
    )


def last_action(app) -> str:
    with app.state.sessions() as db:
        return db.scalar(select(AuditEvent.action).order_by(AuditEvent.seq.desc()))


# --- who sees SC-40 ----------------------------------------------------------------------


def test_admin_sees_their_entities_only(app, admin):
    page = admin.get("/admin/entities")
    assert page.status_code == 200
    assert "Entity One Pvt Ltd" in page.text
    assert "Entity Two Pvt Ltd" not in page.text  # admin of entity 1 only
    assert "<h1>Entities</h1>" in page.text


@pytest.mark.parametrize("who", ["clerk", "approver", "accountant", "auditor"])
def test_non_admins_get_403(app, world, who):
    client = signed_in(app, f"{who}@a.example")
    assert client.get("/admin/entities").status_code == 403
    assert client.get("/admin/entities/1").status_code == 403


def test_admin_of_another_entity_gets_404(app, world):
    admin_b = signed_in(app, "admin@b.example")
    assert admin_b.get("/admin/entities/2").status_code == 200
    assert admin_b.get("/admin/entities/1").status_code == 404
    resp = post(admin_b, "/admin/entities/1", {"legal_name": "Hijack", "pan": PAN_ONE})
    assert resp.status_code == 404
    resp = post(admin_b, "/admin/entities/1/registrations", {"gstin": "x", "address": "y"})
    assert resp.status_code == 404


# --- create and edit entities ------------------------------------------------------------


def test_create_entity_makes_creator_its_admin(app, admin):
    resp = post(admin, "/admin/entities", {"legal_name": "  New  Co Pvt Ltd ", "pan": "zxcpn1234q"})
    assert resp.status_code == 303
    new_id = int(resp.headers["location"].split("/")[3].split("?")[0])
    with app.state.sessions() as db:
        entity = db.get(Entity, new_id)
        assert (entity.legal_name, entity.pan) == ("New Co Pvt Ltd", "ZXCPN1234Q")
        grant = db.scalar(select(RoleGrant).where(RoleGrant.entity_id == new_id))
        assert grant.role == Role.ADMIN.value
        actions = db.scalars(select(AuditEvent.action).where(AuditEvent.entity_id == new_id)).all()
    assert actions == ["entity.created", "user.grants_changed"]
    assert admin.get(f"/admin/entities/{new_id}").status_code == 200


@pytest.mark.parametrize(
    "data,field,message",
    [
        ({"legal_name": "", "pan": "ZXCPN1234Q"}, "legal_name", "Enter the entity"),
        ({"legal_name": "X Ltd", "pan": "12345"}, "pan", "10-character PAN"),
        ({"legal_name": "X Ltd", "pan": PAN_TWO}, "pan", "already exists"),
    ],
)
def test_create_entity_errors_show_next_to_the_field(app, admin, data, field, message):
    resp = post(admin, "/admin/entities", data)
    assert resp.status_code == 422
    assert f'id="e-{field}"' in resp.text and message in resp.text
    assert f'id="f-{field}"' in resp.text and 'aria-invalid="true"' in resp.text
    if data["legal_name"]:
        assert f'value="{data["legal_name"]}"' in resp.text  # the form keeps what was typed


def test_update_entity_is_audited(app, admin):
    resp = post(admin, "/admin/entities/1", {"legal_name": "Entity One Renamed", "pan": PAN_ONE})
    assert resp.status_code == 303
    assert last_action(app) == "entity.updated"
    assert "Entity One Renamed" in admin.get("/admin/entities/1").text


def test_pan_is_fixed_once_registrations_exist(app, admin):
    post(admin, "/admin/entities/1/registrations", {"gstin": gstin("27", PAN_ONE), "address": "A"})
    resp = post(admin, "/admin/entities/1", {"legal_name": "Entity One", "pan": "ZXCPZ9999Z"})
    assert resp.status_code == 422 and "cannot change" in resp.text


# --- GST registrations -------------------------------------------------------------------


def test_add_registration_with_valid_gstin(app, admin):
    g = gstin("27", PAN_ONE)
    spaced = f"{g[:2]} {g[2:12]} {g[12:14]} {g[14]}".lower()
    resp = post(admin, "/admin/entities/1/registrations", {"gstin": spaced, "address": "Pune"})
    assert resp.status_code == 303
    with app.state.sessions() as db:
        reg = db.scalar(select(GstRegistration))
        assert (reg.entity_id, reg.gstin, reg.state_code) == (1, g, "27")
    assert last_action(app) == "gst_registration.added"
    page = admin.get("/admin/entities/1").text
    assert f"{g[:2]} {g[2:12]} {g[12:14]} {g[14]}" in page  # grouped (ux-standards)
    assert "Maharashtra" in page


@pytest.mark.parametrize(
    "value,message",
    [
        (bad_check(gstin("27", PAN_ONE)), "check character"),
        ("27ZXCPA0001A1Z", "does not match the GSTIN format"),
        (gstin("50", PAN_ONE), "not a GST state code"),
        (gstin("27", "ZXCPQ7777Q"), "does not match the entity&#39;s PAN"),
    ],
)
def test_invalid_gstin_cannot_be_saved(app, admin, value, message):
    resp = post(admin, "/admin/entities/1/registrations", {"gstin": value, "address": "Pune"})
    assert resp.status_code == 422
    assert message in resp.text
    assert 'id="e-gstin"' in resp.text
    with app.state.sessions() as db:
        assert db.scalars(select(GstRegistration)).all() == []


def test_same_gstin_cannot_belong_to_two_entities(app, sessions, entity_rows):
    """FR-1 AC. Unique PANs make this unreachable through the form for a correct GSTIN, so
    the service and the database each refuse it on their own."""
    system = Actor.system("test")
    g = gstin("27", PAN_ONE)
    with sessions() as db:
        one, two = db.get(Entity, 1), db.get(Entity, 2)
        add_registration(db, system, one, gstin=g, address="Pune")
        db.commit()
        with pytest.raises(ValidationError, match="this entity"):
            add_registration(db, system, one, gstin=g, address="Again")
        with pytest.raises(ValidationError, match="another entity"):
            add_registration(db, system, two, gstin=g, address="Elsewhere")
        db.rollback()
        db.add(GstRegistration(entity_id=2, gstin=g, state_code="27", address="raw insert"))
        with pytest.raises(IntegrityError):
            db.flush()


def test_registration_address_update_and_remove(app, admin):
    post(admin, "/admin/entities/1/registrations", {"gstin": gstin("29", PAN_ONE), "address": "A"})
    with app.state.sessions() as db:
        reg_id = db.scalar(select(GstRegistration.id))
    url = f"/admin/entities/1/registrations/{reg_id}"
    assert post(admin, url, {"address": ""}).status_code == 422
    assert post(admin, url, {"address": "New address, Bengaluru"}).status_code == 303
    assert last_action(app) == "gst_registration.updated"
    assert post(admin, url + "/remove", {}).status_code == 303
    assert last_action(app) == "gst_registration.removed"
    with app.state.sessions() as db:
        assert db.get(GstRegistration, reg_id) is None


def test_registration_of_another_entity_is_404(app, world):
    with app.state.sessions() as db:
        reg = add_registration(
            db, Actor.system("t"), db.get(Entity, 2), gstin=gstin("33", PAN_TWO), address="C"
        )
        db.commit()
        reg_id = reg.id
    admin_a = signed_in(app, "admin@a.example")
    # Neither through entity 1's URL nor entity 2's.
    assert post(admin_a, f"/admin/entities/1/registrations/{reg_id}/remove", {}).status_code == 404
    assert post(admin_a, f"/admin/entities/2/registrations/{reg_id}/remove", {}).status_code == 404


def test_service_create_entity_by_system_grants_nobody(sessions, engine):
    with sessions() as db:
        e = create_entity(db, Actor.system("seed"), legal_name="Sys Co", pan="ZXCPS1111S")
        db.commit()
        assert db.scalars(select(RoleGrant).where(RoleGrant.entity_id == e.id)).all() == []
