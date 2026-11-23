"""SC-1 Login and logout (data-protection §3; approval-and-audit §5 audits each login)."""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import delete

from app.accounts import passwords
from app.accounts.models import UserSession, as_utc, utcnow
from app.accounts.service import actor_for, find_user_by_email
from app.api.deps import (
    LOGIN_CSRF_COOKIE,
    SESSION_COOKIE,
    Db,
    SignedIn,
    is_https,
    new_token,
    safe_next,
    token_hash,
)
from app.api.templating import render
from app.audit.chain import append_event
from app.domain.actor import Role

router = APIRouter()

LOGIN_FAILED = (
    "Sign-in failed. Check your email and password. After 5 failed attempts in a row "
    "the account is locked for 15 minutes."
)


def landing_page(roles: frozenset[Role]) -> str:
    """screens.md §1: everyone lands on My work; admins with no other role on Admin."""
    return "/admin/entities" if roles == {Role.ADMIN} else "/"


def _login_page(
    request: Request, token: str, *, next_url: str, email: str = "", error: str | None = None
) -> Response:
    resp = render(
        request,
        "login.html",
        {
            "login_csrf": token,
            "next": next_url,
            "email": email,
            "error": error,
            "expired": request.query_params.get("expired") == "1",
            "signed_out": request.query_params.get("signed_out") == "1",
        },
        status_code=401 if error else 200,
    )
    resp.set_cookie(
        LOGIN_CSRF_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        path="/login",
    )
    return resp


@router.get("/login", name="login")
def login_form(request: Request, next: str = "/") -> Response:
    token = request.cookies.get(LOGIN_CSRF_COOKIE) or new_token()
    return _login_page(request, token, next_url=safe_next(next))


@router.post("/login")
def login(
    request: Request,
    db: Db,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> Response:
    settings = request.app.state.settings
    csrf = request.cookies.get(LOGIN_CSRF_COOKIE, "")
    next_url = safe_next(next)
    now = utcnow()
    password = password[: passwords.MAX_LENGTH + 1]
    user = find_user_by_email(db, email) if email else None

    def fail(reason: str) -> Response:
        if user is not None:
            append_event(
                db,
                actor="anonymous",
                action="auth.login_failed",
                data={"user_id": user.id, "reason": reason},
            )
            db.commit()
        return _login_page(request, csrf, next_url=next_url, email=email, error=LOGIN_FAILED)

    if user is None or not user.is_active:
        passwords.burn_verify(password)
        return fail("inactive")
    locked_until = as_utc(user.locked_until)
    if locked_until is not None and locked_until > now:
        passwords.burn_verify(password)
        return fail("locked")
    if not passwords.verify_password(user.password_hash, password):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
            return fail("bad_password_locked")
        return fail("bad_password")

    user.failed_logins = 0
    user.locked_until = None
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)
    # A fresh session token on every sign-in (no session fixation); drop the old one.
    if old := request.cookies.get(SESSION_COOKIE):
        db.execute(delete(UserSession).where(UserSession.token_hash == token_hash(old)))
    token = new_token()
    db.add(
        UserSession(
            token_hash=token_hash(token),
            user_id=user.id,
            csrf_token=new_token(),
            created_at=now,
            last_seen_at=now,
        )
    )
    append_event(db, actor=f"user:{user.id}", action="auth.login", data={"user_id": user.id})
    actor = actor_for(db, user.id)
    db.commit()

    roles = frozenset().union(*actor.roles_by_entity.values())
    target = next_url if next_url != "/" else landing_page(roles)
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=is_https(request),
        path="/",
    )
    resp.delete_cookie(LOGIN_CSRF_COOKIE, path="/login")
    return resp


@router.post("/logout", name="logout")
def logout(request: Request, user: SignedIn, db: Db) -> Response:
    db.execute(delete(UserSession).where(UserSession.token_hash == user.session_hash))
    append_event(db, actor=f"user:{user.id}", action="auth.logout", data={"user_id": user.id})
    db.commit()
    resp = RedirectResponse("/login?signed_out=1", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp
