"""Layout shell (screens.md §1.1), SC-2 My work, SC-6 Account, entity switcher (ticket #10).

SC-2 and SC-6 are for every role; the role that cannot see them is "not signed in", which
`test_route_protection` covers for every route.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts.models import UserSession
from app.api.menu import items_for_roles
from app.api.pages import cards_for
from app.audit.models import AuditEvent
from app.domain.actor import Role
from tests.conftest import PASSWORD, add_user, csrf_of, grants, login, signed_in

NEW_PASSWORD = "a brand new long password"


def menu_labels(html: str) -> list[str]:
    nav = re.search(r'<nav class="side".*?</nav>', html, re.S).group(0)
    return re.findall(r"<a href=\"[^\"]+\"[^>]*>([^<]+)</a>", nav)


# --- shell and menus (screens.md §2) ---------------------------------------------------


def test_menu_matrix_matches_screens_md():
    expected = {
        Role.CLERK: ["My work", "Invoices", "Upload", "Vendors"],
        Role.APPROVER: ["My work", "Invoices", "Approvals"],
        Role.ACCOUNTANT: ["My work", "Invoices", "Export", "Vendors"],
        Role.ADMIN: ["My work", "Invoices", "Vendors", "Admin"],
        Role.AUDITOR: ["My work", "Invoices", "Vendors", "Audit"],
    }
    for role, labels in expected.items():
        assert [i.label for i in items_for_roles({role})] == labels, role
    union = [i.label for i in items_for_roles({Role.CLERK, Role.APPROVER})]
    assert union == ["My work", "Invoices", "Upload", "Approvals", "Vendors"]


@pytest.mark.parametrize(
    "who,labels",
    [
        ("clerk", ["My work"]),
        ("approver", ["My work"]),
        ("accountant", ["My work"]),
        ("auditor", ["My work"]),
        ("admin", ["My work", "Admin", "Entities", "Users"]),
    ],
)
def test_rendered_menu_shows_only_built_pages_for_the_role(app, world, who, labels):
    page = signed_in(app, f"{who}@a.example").get("/")
    assert menu_labels(page.text) == labels


def test_shell_has_header_user_menu_and_live_region(app, world):
    page = signed_in(app, "clerk@a.example").get("/").text
    assert 'aria-live="polite"' in page
    assert 'href="/account"' in page and 'action="/logout"' in page
    assert 'aria-label="Main menu"' in page and 'aria-controls="main-menu"' in page
    assert "@media (max-width: 767px)" in page  # the menu collapses below 768 px
    assert "Entity: Entity One Pvt Ltd" in page  # one entity: shown, no switcher


def test_login_page_has_no_shell(client):
    page = client.get("/login").text
    assert '<nav class="side"' not in page


# --- SC-2 My work -------------------------------------------------------------------------


def test_cards_per_role():
    assert [c.title for c in cards_for(frozenset({Role.CLERK}))] == ["To review"]
    assert [c.title for c in cards_for(frozenset({Role.APPROVER}))] == ["To approve"]
    assert [c.title for c in cards_for(frozenset({Role.ACCOUNTANT}))] == [
        "To export",
        "To mark paid",
    ]
    assert [c.title for c in cards_for(frozenset({Role.ADMIN}))] == ["Quarantine"]
    assert cards_for(frozenset({Role.AUDITOR})) == []


@pytest.mark.parametrize(
    "who,title",
    [("clerk", "To review"), ("approver", "To approve"), ("accountant", "To export")],
)
def test_my_work_shows_empty_cards_for_the_role(app, world, who, title):
    page = signed_in(app, f"{who}@a.example").get("/")
    assert page.status_code == 200
    assert "<h1>My work</h1>" in page.text
    assert f">{title}</h2>" in page.text
    assert "Quarantine" not in page.text


def test_my_work_for_auditor_and_for_user_without_access(app, world, sessions):
    assert "no task queue" in signed_in(app, "auditor@a.example").get("/").text
    add_user(sessions, "lonely@a.example", {})
    assert "no access to any entity" in signed_in(app, "lonely@a.example").get("/").text


# --- entity switcher ----------------------------------------------------------------------


@pytest.fixture
def multi(app, world, sessions):
    add_user(sessions, "multi@x.example", {1: grants(Role.CLERK), 2: grants(Role.APPROVER)})
    return signed_in(app, "multi@x.example")


def test_switcher_lists_only_granted_entities(multi):
    page = multi.get("/").text
    assert 'id="entity-switch"' in page
    assert "All my entities" in page
    assert ">Entity One Pvt Ltd</option>" in page and ">Entity Two Pvt Ltd</option>" in page
    assert {"To review", "To approve"} <= set(re.findall(r">([^<]+)</h2>", page))


def test_switching_entity_narrows_the_page(app, multi):
    token = csrf_of(multi, "/")
    resp = multi.post(
        "/switch-entity",
        data={"entity_id": "2", "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/?msg=entity_switched"
    page = multi.get("/").text
    assert "Entity: Entity Two Pvt Ltd" in page
    titles = re.findall(r">([^<]+)</h2>", page)
    assert "To approve" in titles and "To review" not in titles
    multi.post("/switch-entity", data={"entity_id": "", "csrf_token": token})
    assert "Entity: All my entities" in multi.get("/").text


def test_switching_to_an_entity_without_a_grant_is_404(app, world):
    clerk = signed_in(app, "clerk@a.example")
    token = csrf_of(clerk, "/")
    for bad in ("2", "999", "abc"):
        resp = clerk.post("/switch-entity", data={"entity_id": bad, "csrf_token": token})
        assert resp.status_code == 404, bad
    with app.state.sessions() as db:
        assert db.scalar(select(UserSession.entity_id)) is None


# --- SC-6 Account ------------------------------------------------------------------------


def test_account_shows_roles_per_entity(app, world, sessions):
    add_user(
        sessions, "multi@x.example", {1: grants(Role.APPROVER, tier=2), 2: grants(Role.AUDITOR)}
    )
    page = signed_in(app, "multi@x.example").get("/account")
    assert page.status_code == 200
    assert "approver (tier 2)" in page.text and "auditor" in page.text
    assert "Entity One Pvt Ltd" in page.text and "Entity Two Pvt Ltd" in page.text


def test_account_change_name(app, world):
    client = signed_in(app, "clerk@a.example")
    token = csrf_of(client)
    resp = client.post("/account/profile", data={"name": "", "csrf_token": token})
    assert resp.status_code == 422 and "Enter your name" in resp.text
    resp = client.post("/account/profile", data={"name": "Clerk Renamed", "csrf_token": token})
    assert resp.status_code == 200 and "Clerk Renamed" in resp.text  # followed the redirect


@pytest.mark.parametrize(
    "current,new,confirm,field",
    [
        ("wrong password!!", NEW_PASSWORD, NEW_PASSWORD, "current_password"),
        (PASSWORD, "short", "short", "new_password"),
        (PASSWORD, NEW_PASSWORD, NEW_PASSWORD + "x", "confirm_password"),
    ],
)
def test_change_password_errors(app, world, current, new, confirm, field):
    client = signed_in(app, "clerk@a.example")
    resp = client.post(
        "/account/password",
        data={
            "current_password": current,
            "new_password": new,
            "confirm_password": confirm,
            "csrf_token": csrf_of(client),
        },
    )
    assert resp.status_code == 422
    assert f'id="e-{field}"' in resp.text


def test_change_password_signs_out_other_sessions(app, world):
    here = signed_in(app, "clerk@a.example")
    elsewhere = signed_in(app, "clerk@a.example")
    resp = here.post(
        "/account/password",
        data={
            "current_password": PASSWORD,
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
            "csrf_token": csrf_of(here),
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert here.get("/").status_code == 200
    assert elsewhere.get("/", follow_redirects=False).status_code == 303
    assert login(TestClient(app), "clerk@a.example").status_code == 401
    assert login(TestClient(app), "clerk@a.example", NEW_PASSWORD).status_code == 303
    with app.state.sessions() as db:
        actions = db.scalars(select(AuditEvent.action)).all()
    assert "user.password_changed" in actions


def test_status_message_is_announced(app, world):
    page = signed_in(app, "clerk@a.example").get("/account?msg=profile_saved").text
    assert re.search(r'aria-live="polite">Your name was saved.</div>', page)
    page = signed_in(app, "clerk@a.example").get("/account?msg=<script>").text
    assert "<script>" not in page.split('aria-live="polite">')[1]


def test_404_page_for_unknown_path(app, world):
    resp = signed_in(app, "clerk@a.example").get("/no-such-page")
    assert resp.status_code == 404 and "Page not found" in resp.text
