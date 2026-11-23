"""Jinja templates, ux-standards formatting filters, and the page renderer."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import HTMLResponse

from app.api.menu import links_for
from app.domain.money import Money
from app.domain.states import InvoiceState

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


def _indian_grouping(digits: str) -> str:
    """`1234567` -> `12,34,567`: the last three digits, then groups of two."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def format_money(value: Money | Decimal | None, currency: str = "INR") -> str:
    """ux-standards: `₹1,23,456.00` for INR; other currencies `USD 1,234.00`.

    The one way templates show money (`{{ amount|money }}`). Takes `Money` or `Decimal`
    rupees, never `float`.
    """
    if value is None:
        return ""
    if isinstance(value, Money):
        amount = value.amount
    elif isinstance(value, Decimal):
        amount = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        raise TypeError(f"money filter takes Money or Decimal, not {type(value).__name__}")
    sign = "-" if amount < 0 else ""
    whole, _, paise = f"{abs(amount):.2f}".partition(".")
    if currency == "INR":
        return f"{sign}₹{_indian_grouping(whole)}.{paise}"
    return f"{sign}{currency} {int(whole):,}.{paise}"


# Invoice states as stamps (visual-design.md §4): the word is always shown, never colour alone.
STATE_LABELS: dict[InvoiceState, str] = {
    InvoiceState.RECEIVED: "Received",
    InvoiceState.EXTRACTING: "Extracting",
    InvoiceState.EXTRACTION_FAILED: "Extraction failed",
    InvoiceState.NEEDS_REVIEW: "Needs review",
    InvoiceState.READY_FOR_APPROVAL: "Ready for approval",
    InvoiceState.IN_APPROVAL: "In approval",
    InvoiceState.REJECTED: "Rejected",
    InvoiceState.APPROVED: "Approved",
    InvoiceState.EXPORTED: "Exported",
    InvoiceState.PAID: "Paid",
    InvoiceState.DISCARDED: "Discarded",
}


def state_label(state: InvoiceState | str) -> str:
    return STATE_LABELS[InvoiceState(state)]


TEMPLATES.env.filters["gstin"] = format_gstin
TEMPLATES.env.filters["date"] = format_date
TEMPLATES.env.filters["money"] = format_money
TEMPLATES.env.filters["state_label"] = state_label


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
