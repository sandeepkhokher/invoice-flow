"""Every route except /login and /health requires sign-in (data-protection §3, ticket #10).

The test walks the app's real route table, so a route added later is covered automatically.
"""

import re

from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Mount

from app.api.deps import PUBLIC_PATHS

PUBLIC = {("/login", "GET"), ("/login", "POST"), ("/health", "GET")}


def _routes(app):
    """(path, method) for every route, with included routers' prefixes applied."""
    for ctx in iter_route_contexts(app.routes):
        route = ctx.original_route
        assert isinstance(route, APIRoute), f"unexpected non-API route {route!r}"
        for method in sorted(route.methods - {"HEAD"}):
            yield ctx.path, method


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", path)


def test_public_paths_are_exactly_login_and_health():
    assert {"/login", "/health"} == PUBLIC_PATHS


def test_public_routes_are_exactly_login_and_health(app):
    public = {(p, m) for p, m in _routes(app) if p in PUBLIC_PATHS}
    assert public == PUBLIC


def test_no_mounts_or_api_docs(app, client):
    assert not any(isinstance(c.original_route, Mount) for c in iter_route_contexts(app.routes))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path, follow_redirects=False).status_code == 404


def test_every_other_route_requires_login(app, client):
    checked = 0
    for path, method in _routes(app):
        if path in PUBLIC_PATHS:
            continue
        resp = client.request(method, _concrete(path), follow_redirects=False)
        if method == "GET":
            assert resp.status_code == 303, (method, path, resp.status_code)
            assert resp.headers["location"].startswith("/login?next="), path
        else:
            assert resp.status_code == 401, (method, path, resp.status_code)
        checked += 1
    assert checked >= 15  # the S5 screens' routes are all in the table


def test_a_bogus_session_cookie_is_not_a_login(app, client):
    client.cookies.set("if_session", "not-a-real-token")
    assert client.get("/", follow_redirects=False).status_code == 303


def test_a_new_route_is_protected_by_default(app, client):
    @app.get("/added-later")
    def added_later() -> dict:
        return {"secret": "data"}

    resp = client.get("/added-later", follow_redirects=False)
    assert resp.status_code == 303 and "secret" not in resp.text


def test_health_and_login_work_without_a_session(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}
    assert client.get("/login").status_code == 200
