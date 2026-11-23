"""The entity-scoped repository (data-protection §2, FR-3, ticket #10).

A user of entity A gets 404, not 403, for entity B's invoice; a user with no grant in an
entity sees nothing from it.
"""

import pytest
from fastapi import Request
from sqlalchemy import select

from app.accounts.models import Entity, GstRegistration
from app.accounts.service import actor_for
from app.api.deps import current_user
from app.domain import transitions as t
from app.domain.actor import Actor, Role
from app.domain.models import Invoice
from app.scoping import EntityScope, Forbidden, NotFound
from tests.conftest import signed_in

SYSTEM = Actor.system("intake")


@pytest.fixture
def invoices(app, world):
    """One invoice in entity 1 and one in entity 2; returns their ids."""
    with app.state.sessions() as db:
        a = t.create_invoice(db, SYSTEM, entity_id=1, source="upload")
        b = t.create_invoice(db, SYSTEM, entity_id=2, source="upload")
        db.commit()
        return {"a": a.id, "b": b.id}


@pytest.fixture
def invoice_route(app):
    """A minimal invoice page that uses the repository, as the S11 invoice pages will."""

    @app.get("/test-invoices/{invoice_id}")
    def show(invoice_id: int, request: Request) -> dict:
        user = current_user(request.state.user)
        with request.app.state.sessions() as db:
            inv = user.scope.get(db, Invoice, invoice_id)
            return {"id": inv.id, "entity": inv.entity_id}

    return "/test-invoices/{}"


def test_user_of_entity_a_gets_404_for_entity_b_invoice(app, invoices, invoice_route):
    clerk_a = signed_in(app, "clerk@a.example")
    own = clerk_a.get(invoice_route.format(invoices["a"]))
    assert own.status_code == 200 and own.json()["entity"] == 1

    other = clerk_a.get(invoice_route.format(invoices["b"]))
    missing = clerk_a.get(invoice_route.format(999_999))
    assert other.status_code == 404  # not 403
    assert missing.status_code == 404
    assert other.text == missing.text  # "not yours" looks exactly like "does not exist"


def test_user_of_entity_b_gets_404_for_entity_a_invoice(app, invoices, invoice_route):
    clerk_b = signed_in(app, "clerk@b.example")
    assert clerk_b.get(invoice_route.format(invoices["a"])).status_code == 404
    assert clerk_b.get(invoice_route.format(invoices["b"])).status_code == 200


def test_scope_lists_only_granted_entities(app, invoices):
    with app.state.sessions() as db:
        scope_a = EntityScope(actor_for(db, _uid(db, "clerk@a.example")))
        scope_b = EntityScope(actor_for(db, _uid(db, "clerk@b.example")))
        assert [i.id for i in scope_a.list(db, Invoice)] == [invoices["a"]]
        assert [i.id for i in scope_b.list(db, Invoice)] == [invoices["b"]]
        with pytest.raises(NotFound):
            scope_a.get(db, Invoice, invoices["b"])


def test_user_without_any_grant_sees_nothing(app, invoices):
    nobody = EntityScope(Actor.user(12345, {}))
    with app.state.sessions() as db:
        assert nobody.list(db, Invoice) == []
        assert nobody.list(db, Entity) == []
        assert nobody.list(db, GstRegistration) == []
        for inv_id in invoices.values():
            with pytest.raises(NotFound):
                nobody.get(db, Invoice, inv_id)
    with pytest.raises(Forbidden):
        nobody.require_any(Role.ADMIN)


def test_permission_without_role_gives_no_visibility(app, invoices):
    from app.domain.actor import Permission

    actor = Actor.user(1, {}, permissions_by_entity={2: {Permission.BANK_DETAILS_VIEW}})
    with app.state.sessions() as db:
        assert EntityScope(actor).list(db, Invoice) == []


def test_role_filter_and_switcher_narrowing(app, invoices):
    actor = Actor.user(1, {1: {Role.CLERK}, 2: {Role.AUDITOR}})
    scope = EntityScope(actor)
    assert scope.entity_ids() == {1, 2}
    assert scope.entity_ids(Role.CLERK) == {1}
    assert scope.roles() == {Role.CLERK, Role.AUDITOR}
    narrowed = EntityScope(actor, selected_entity_id=2)
    assert narrowed.listing_entity_ids() == {2}
    assert narrowed.roles() == {Role.AUDITOR}
    with app.state.sessions() as db:
        # Lists follow the switcher; lookups by id still work across the user's entities.
        assert [i.entity_id for i in narrowed.list(db, Invoice)] == [2]
        assert narrowed.get(db, Invoice, invoices["a"]).entity_id == 1
        with pytest.raises(NotFound):
            scope.get(db, Invoice, invoices["b"], Role.CLERK)  # auditor there, not clerk
    with pytest.raises(NotFound):
        scope.require_entity(3)


def test_routes_do_not_filter_entities_themselves():
    """Entity filtering lives in one place: no route module filters on entity_id directly."""
    from pathlib import Path

    api = Path(__file__).resolve().parent.parent / "app" / "api"
    for path in api.glob("*.py"):
        text = path.read_text()
        assert ".entity_id.in_(" not in text, path.name
        assert "entity_id ==" not in text, path.name


def _uid(db, email):
    from app.accounts.models import User

    return db.scalar(select(User.id).where(User.email == email))
