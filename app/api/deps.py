"""Who is signed in, and CSRF: one global dependency for every route (data-protection §3).

`guard` is installed on the whole app (`FastAPI(dependencies=[...])`), so a new route is
login-protected by default. Only the paths in `PUBLIC_PATHS` skip it, and a test enumerates
the app's routes to prove it. Sessions are server-side (`user_sessions`); the cookie holds a
random token, HttpOnly, SameSite=Lax, and Secure when the request came over HTTPS.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import Entity, User, UserSession, as_utc, utcnow
from app.accounts.service import actor_for
from app.domain.actor import Actor
from app.scoping import EntityScope

SESSION_COOKIE = "if_session"
LOGIN_CSRF_COOKIE = "if_login_csrf"
CSRF_FIELD = "csrf_token"
CSRF_HEADER = "x-csrf-token"
PUBLIC_PATHS = frozenset({"/login", "/health"})
STATE_CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Refresh `last_seen_at` at most once a minute, to keep reads from writing on every request.
TOUCH_INTERVAL = timedelta(seconds=60)


class LoginRequired(Exception):
    def __init__(self, *, expired: bool = False) -> None:
        super().__init__("sign-in required")
        self.expired = expired


class CsrfFailed(Exception):
    """A state-changing request without a valid CSRF token."""


@dataclass(frozen=True)
class CurrentUser:
    id: int
    email: str
    name: str
    session_hash: str
    csrf_token: str
    actor: Actor
    selected_entity_id: int | None
    # (id, legal name) of every entity the user holds a role on, for the switcher.
    entities: tuple[tuple[int, str], ...] = field(default_factory=tuple)

    @property
    def scope(self) -> EntityScope:
        return EntityScope(self.actor, self.selected_entity_id)

    @property
    def selected_entity_name(self) -> str | None:
        return dict(self.entities).get(self.selected_entity_id)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def is_https(request: Request) -> bool:
    return request.url.scheme == "https"


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessions() as db:
        yield db


def authenticate(request: Request) -> CurrentUser | None:
    """Load the signed-in user from the session cookie, or raise `LoginRequired`."""
    if request.url.path in PUBLIC_PATHS:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise LoginRequired
    settings = request.app.state.settings
    with request.app.state.sessions() as db:
        sess = db.get(UserSession, token_hash(token))
        if sess is None:
            raise LoginRequired
        now = utcnow()
        idle = now - as_utc(sess.last_seen_at)
        if idle > timedelta(minutes=settings.session_idle_minutes):
            db.delete(sess)
            db.commit()
            raise LoginRequired(expired=True)
        user = db.get(User, sess.user_id)
        if user is None or not user.is_active:
            db.delete(sess)
            db.commit()
            raise LoginRequired
        actor = actor_for(db, user.id)
        entity_ids = sorted(actor.roles_by_entity)
        entities = tuple(
            (row.id, row.legal_name)
            for row in db.execute(
                select(Entity.id, Entity.legal_name)
                .where(Entity.id.in_(entity_ids))
                .order_by(Entity.legal_name)
            )
        )
        selected = sess.entity_id if sess.entity_id in actor.roles_by_entity else None
        if idle > TOUCH_INTERVAL:
            sess.last_seen_at = now
            db.commit()
        current = CurrentUser(
            id=user.id,
            email=user.email,
            name=user.name,
            session_hash=sess.token_hash,
            csrf_token=sess.csrf_token,
            actor=actor,
            selected_entity_id=selected,
            entities=entities,
        )
    request.state.user = current
    return current


MaybeUser = Annotated[CurrentUser | None, Depends(authenticate)]


async def guard(request: Request, user: MaybeUser) -> None:
    """Every state-changing request carries a CSRF token: the session's, or for `/login`
    (no session yet) the double-submit token from the login form's cookie."""
    if request.method not in STATE_CHANGING:
        return
    expected = user.csrf_token if user else request.cookies.get(LOGIN_CSRF_COOKIE)
    supplied = request.headers.get(CSRF_HEADER)
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith(("application/x-www-form-urlencoded", "multipart/form-data")):
            supplied = (await request.form()).get(CSRF_FIELD)
    if (
        not expected
        or not isinstance(supplied, str)
        or not secrets.compare_digest(expected, supplied)
    ):
        raise CsrfFailed


def current_user(user: MaybeUser) -> CurrentUser:
    if user is None:
        raise LoginRequired
    return user


SignedIn = Annotated[CurrentUser, Depends(current_user)]
Db = Annotated[Session, Depends(get_db)]


def safe_next(target: str | None) -> str:
    """Only same-site relative paths, so `?next=` cannot redirect off the app."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target
