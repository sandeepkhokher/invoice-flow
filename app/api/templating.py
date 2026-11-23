"""Jinja templates, ux-standards formatting filters, and the page renderer."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from app.api.menu import links_for

TEMPLATES = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# Status messages after a redirect (`?msg=`). Only these fixed texts are ever shown.
MESSAGES = {
    "entity_created": "Entity created.",
    "entity_saved": "Entity saved.",
    "registration_added": "GST registration added.",
    "registration_saved": "GST registration saved.",
    "registration_removed": "GST registration removed.",
    "grants_saved": "Roles saved.",
    "user_deactivated": "User deactivated. Their sessions were signed out.",
    "user_reactivated": "User reactivated.",
    "profile_saved": "Your name was saved.",
    "password_changed": "Password changed. Your other sessions were signed out.",
    "entity_switched": "Entity switched.",
}


def format_gstin(gstin: str | None) -> str:
    """ux-standards: `27 AAPFU0939F 1Z V` (state, PAN, entity + Z, check)."""
    if not gstin or len(gstin) != 15:
        return gstin or ""
    return f"{gstin[:2]} {gstin[2:12]} {gstin[12:14]} {gstin[14]}"


def format_date(value: date | datetime | None) -> str:
    """ux-standards: `20-Nov-2026`."""
    if value is None:
        return ""
    return value.strftime("%d-%b-%Y")


TEMPLATES.env.filters["gstin"] = format_gstin
TEMPLATES.env.filters["date"] = format_date


def render(
    request: Request,
    name: str,
    context: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    user = getattr(request.state, "user", None)
    ctx: dict[str, Any] = {
        "current_user": user,
        "csrf_token": user.csrf_token if user else None,
        "menu": links_for(request.app, user.scope.roles()) if user else [],
        "message": MESSAGES.get(request.query_params.get("msg", "")),
        "errors": {},
        "form": {},
    }
    ctx.update(context or {})
    return TEMPLATES.TemplateResponse(request, name, ctx, status_code=status_code)
