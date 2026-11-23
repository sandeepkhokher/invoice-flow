"""S5 sign-in, sessions and CSRF (data-protection §3; SC-1 Login)."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.accounts import passwords
from app.accounts.models import User, UserSession, utcnow
from app.api.auth import LOGIN_FAILED
from app.api.deps import LOGIN_CSRF_COOKIE, SESSION_COOKIE, safe_next, token_hash
from app.audit.models import AuditEvent
from tests.conftest import PASSWORD, csrf_of, login, signed_in


def _db(app):
    return app.state.sessions()


def _set_cookie_headers(resp) -> list[str]:
    return [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]


# --- passwords ---------------------------------------------------------------------------


def test_password_hashes_are_argon2id(app, world):
    with _db(app) as db:
        hashes = db.scalars(select(User.password_hash)).all()
    assert hashes and all(h.startswith("$argon2id$") for h in hashes)
    assert PASSWORD not in "".join(hashes)


def test_production_hasher_is_argon2id(monkeypatch):
    from argon2 import Type

    monkeypatch.undo()  # drop the cheap test parameters: check the real hasher
    assert passwords.HASHER.type == Type.ID
    assert passwords.hash_password("a long enough password").startswith("$argon2id$")


def test_verify_password_rejects_wrong_and_garbage():
    h = passwords.hash_password("a long enough password")
    assert passwords.verify_password(h, "a long enough password")
    assert not passwords.verify_password(h, "wrong password here")
    assert not passwords.verify_password("not-a-hash", "whatever")


# --- SC-1 login --------------------------------------------------------------------------


def test_login_page_is_public_and_sets_login_csrf_cookie(client):
    resp = client.get("/login")
    assert resp.status_code == 200
    assert 'name="csrf_token"' in resp.text and 'autocomplete="current-password"' in resp.text
    cookie = next(c for c in _set_cookie_headers(resp) if c.startswith(LOGIN_CSRF_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_login_success_sets_httponly_samesite_lax_session_cookie(app, world):
    client = TestClient(app)
    resp = login(client, "clerk@a.example")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    cookie = next(c for c in _set_cookie_headers(resp) if c.startswith(SESSION_COOKIE + "="))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Secure" not in cookie  # plain http in tests
    assert "Max-Age" not in cookie and "expires" not in cookie.lower()  # a session cookie
    assert client.get("/").status_code == 200


def test_session_cookie_is_secure_over_https(app, world):
    client = TestClient(app, base_url="https://testserver")
    resp = login(client, "clerk@a.example")
    cookie = next(c for c in _set_cookie_headers(resp) if c.startswith(SESSION_COOKIE + "="))
    assert "Secure" in cookie


def test_session_token_is_stored_only_as_a_hash(app, world):
    client = TestClient(app)
    login(client, "clerk@a.example")
    token = client.cookies.get(SESSION_COOKIE)
    with _db(app) as db:
        rows = db.scalars(select(UserSession)).all()
    assert [r.token_hash for r in rows] == [token_hash(token)]
    assert token not in {r.token_hash for r in rows}


@pytest.mark.parametrize(
    "email,password",
    [("clerk@a.example", "wrong password!!"), ("nobody@a.example", PASSWORD), ("", "")],
)
def test_login_failure_is_generic(app, world, email, password):
    client = TestClient(app)
    resp = login(client, email, password)
    assert resp.status_code == 401
    assert LOGIN_FAILED in resp.text
    assert SESSION_COOKIE not in client.cookies
    assert f'value="{email}"' in resp.text  # the form keeps what was typed


def test_lockout_after_repeated_failures(app, world):
    client = TestClient(app)
    for _ in range(app.state.settings.login_max_failures):
        assert login(client, "clerk@a.example", "wrong password!!").status_code == 401
    # Locked: even the right password fails, with the same generic message.
    resp = login(client, "clerk@a.example")
    assert resp.status_code == 401 and LOGIN_FAILED in resp.text
    with _db(app) as db:
        user = db.scalar(select(User).where(User.email == "clerk@a.example"))
        assert user.locked_until is not None
        user.locked_until = utcnow() - timedelta(seconds=1)  # the lock has run out
        db.commit()
    assert login(client, "clerk@a.example").status_code == 303


def test_successful_login_resets_failure_count(app, world):
    client = TestClient(app)
    for _ in range(app.state.settings.login_max_failures - 1):
        login(client, "clerk@a.example", "wrong password!!")
    assert login(client, "clerk@a.example").status_code == 303
    with _db(app) as db:
        assert db.scalar(select(User.failed_logins).where(User.email == "clerk@a.example")) == 0


def test_login_without_or_with_wrong_csrf_is_refused(app, world):
    client = TestClient(app)
    client.get("/login")
    data = {"email": "clerk@a.example", "password": PASSWORD}
    assert client.post("/login", data=data, follow_redirects=False).status_code == 403
    bad = {**data, "csrf_token": "forged"}
    assert client.post("/login", data=bad, follow_redirects=False).status_code == 403
    assert SESSION_COOKIE not in client.cookies


def test_login_is_audited(app, world):
    client = TestClient(app)
    login(client, "clerk@a.example", "wrong password!!")
    login(client, "clerk@a.example")
    with _db(app) as db:
        actions = db.scalars(select(AuditEvent.action).order_by(AuditEvent.seq)).all()
    assert actions[-2:] == ["auth.login_failed", "auth.login"]


def test_login_rotates_the_session_token(app, world):
    client = TestClient(app)
    login(client, "clerk@a.example")
    first = client.cookies.get(SESSION_COOKIE)
    login(client, "clerk@a.example")
    second = client.cookies.get(SESSION_COOKIE)
    assert first != second
    with _db(app) as db:
        assert db.get(UserSession, token_hash(first)) is None


def test_login_redirects_to_safe_next_only(app, world):
    client = TestClient(app)
    page = client.get("/login?next=/account")
    assert 'name="next" value="/account"' in page.text
    resp = login(client, "clerk@a.example")  # helper posts next=/
    assert resp.headers["location"] == "/"
    for bad in ("https://evil.example/", "//evil.example", "/\\evil.example", "account"):
        assert safe_next(bad) == "/"
    assert safe_next("/admin/users?x=1") == "/admin/users?x=1"


def test_unauthenticated_page_redirects_to_login_with_next(client):
    resp = client.get("/account", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=%2Faccount"


def test_admin_only_user_lands_on_admin(app, world):
    resp = login(TestClient(app), "admin@a.example")
    assert resp.headers["location"] == "/admin/entities"


# --- logout and idle timeout -------------------------------------------------------------


def test_logout_ends_the_session(app, world):
    client = signed_in(app, "clerk@a.example")
    token = csrf_of(client)
    resp = client.post("/logout", data={"csrf_token": token}, follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/login?signed_out=1"
    with _db(app) as db:
        assert db.scalars(select(UserSession)).all() == []
        assert db.scalar(select(AuditEvent.action).order_by(AuditEvent.seq.desc())) == "auth.logout"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_logout_needs_csrf(app, world):
    client = signed_in(app, "clerk@a.example")
    assert client.post("/logout", follow_redirects=False).status_code == 403
    assert client.get("/").status_code == 200  # still signed in


def _age_session(app, minutes: int) -> None:
    with _db(app) as db:
        for s in db.scalars(select(UserSession)):
            s.last_seen_at = utcnow() - timedelta(minutes=minutes)
        db.commit()


def test_idle_session_expires_after_30_minutes(app, world):
    assert app.state.settings.session_idle_minutes == 30
    client = signed_in(app, "clerk@a.example")
    _age_session(app, 31)
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=%2F&expired=1"
    with _db(app) as db:
        assert db.scalars(select(UserSession)).all() == []  # the expired session is gone
    assert "30 minutes without activity" in client.get(resp.headers["location"]).text


def test_activity_keeps_the_session_alive(app, world):
    client = signed_in(app, "clerk@a.example")
    _age_session(app, 29)
    assert client.get("/").status_code == 200
    with _db(app) as db:
        seen = db.scalar(select(UserSession.last_seen_at))
    assert utcnow() - seen.replace(tzinfo=utcnow().tzinfo) < timedelta(minutes=1)


def test_htmx_request_without_session_gets_hx_redirect(client):
    resp = client.get("/account", headers={"HX-Request": "true"}, follow_redirects=False)
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"].startswith("/login")


# --- CSRF on state-changing requests -----------------------------------------------------


def test_state_changing_requests_need_the_session_csrf_token(app, world):
    client = signed_in(app, "clerk@a.example")
    url = "/account/profile"
    assert client.post(url, data={"name": "X"}, follow_redirects=False).status_code == 403
    forged = {"name": "X", "csrf_token": "forged"}
    assert client.post(url, data=forged, follow_redirects=False).status_code == 403
    token = csrf_of(client)
    ok = client.post(url, data={"name": "New Name", "csrf_token": token}, follow_redirects=False)
    assert ok.status_code == 303
    # HTMX sends the token as a header instead (hx-headers on <body>).
    hdr = client.post(
        url, data={"name": "Newer Name"}, headers={"X-CSRF-Token": token}, follow_redirects=False
    )
    assert hdr.status_code == 303


def test_another_sessions_csrf_token_is_refused(app, world):
    a = signed_in(app, "clerk@a.example")
    b = signed_in(app, "approver@a.example")
    resp = a.post("/account/profile", data={"name": "X", "csrf_token": csrf_of(b)})
    assert resp.status_code == 403


def test_deactivated_user_session_ends(app, world):
    client = signed_in(app, "clerk@a.example")
    with _db(app) as db:
        db.scalar(select(User).where(User.email == "clerk@a.example")).is_active = False
        db.commit()
    assert client.get("/", follow_redirects=False).status_code == 303
    assert login(TestClient(app), "clerk@a.example").status_code == 401
